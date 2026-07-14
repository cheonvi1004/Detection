"""engine/orchestrator.py — 4개 엔진 통합 오케스트레이터 + 메인 루프"""
from __future__ import annotations
import time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from config.settings import settings
from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult, ZoneStatus
from engine.base import BaseDetectionEngine
from engine.fire_gas import FireGasEngine
from engine.flood import FloodEngine
from engine.condensation import CondensationEngine
from engine.structure import StructureEngine
from alert.notifier import Notifier
from infra.influx_client import InfluxRepo
from infra.pg_client import PgRepo
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class _Debounce:
    candidate: AlertLevel = AlertLevel.NONE
    since:     Optional[float] = None


class Orchestrator:
    def __init__(self) -> None:
        self.influx   = InfluxRepo()
        self.pg       = PgRepo()
        self.notifier = Notifier()
        self.pg.connect()

        self.engines: dict[DetectionDomain, BaseDetectionEngine] = {
            DetectionDomain.FIRE_GAS:     FireGasEngine(self.influx, self.pg),
            DetectionDomain.FLOOD:        FloodEngine(self.influx, self.pg),
            DetectionDomain.CONDENSATION: CondensationEngine(self.influx, self.pg),
            DetectionDomain.STRUCTURE:    StructureEngine(self.influx, self.pg),
        }
        self._prev:     dict[str, AlertLevel]  = {}
        self._debounce: dict[str, _Debounce]   = {}
        self._stop      = threading.Event()

    # ── 단일 구역 평가 ────────────────────────────────────────────
    def evaluate_zone(self, zone_id: str) -> ZoneStatus:
        results: dict[DetectionDomain, DomainResult] = {}
        with ThreadPoolExecutor(max_workers=4) as exe:
            futs = {exe.submit(eng._safe_evaluate, zone_id): dom
                    for dom, eng in self.engines.items()}
            for f in as_completed(futs):
                dom = futs[f]
                try:    results[dom] = f.result()
                except Exception as e:
                    log.error("[%s] %s 예외: %s", zone_id, dom.value, e)
                    results[dom] = DomainResult(zone_id=zone_id, domain=dom,
                                                level=AlertLevel.NONE, detail=str(e))
        zone_level = max((r.level for r in results.values()), default=AlertLevel.NONE)
        return ZoneStatus(zone_id=zone_id, zone_level=zone_level,
                          domain_results=results,
                          previous_level=self._prev.get(zone_id, AlertLevel.NONE))

    # ── Debounce ─────────────────────────────────────────────────
    def _debounce_level(self, zone_id: str, status: ZoneStatus) -> AlertLevel:
        new  = status.zone_level
        prev = status.previous_level
        if new <= prev:
            self._debounce.pop(zone_id, None)
            return new

        wait = settings.debounce.get(new.value)
        if wait == 0:
            self._debounce.pop(zone_id, None)
            return new

        now   = time.time()
        state = self._debounce.get(zone_id)
        if state is None or state.candidate != new:
            self._debounce[zone_id] = _Debounce(candidate=new, since=now)
            log.debug("[%s] debounce 시작: %s→%s (%ds)", zone_id, prev.label, new.label, wait)
            return prev

        if now - state.since >= wait:
            self._debounce.pop(zone_id, None)
            log.info("[%s] debounce 완료: %s→%s", zone_id, prev.label, new.label)
            return new

        log.debug("[%s] debounce 대기 %.1f/%.0fs", zone_id, now - state.since, wait)
        return prev

    # ── 상태 변화 처리 ────────────────────────────────────────────
    def _handle(self, status: ZoneStatus, confirmed: AlertLevel) -> None:
        zone_id = status.zone_id
        prev    = self._prev.get(zone_id, AlertLevel.NONE)
        self._prev[zone_id] = confirmed
        status.zone_level   = confirmed

        if confirmed > prev:
            log.warning("⬆ [%s] %s → %s", zone_id, prev.label, confirmed.label)
            actions = self.notifier.notify(status)
            self._record(status, "ESCALATED" if prev > AlertLevel.NONE else "TRIGGERED", actions)
        elif confirmed < prev:
            log.info("⬇ [%s] %s → %s", zone_id, prev.label, confirmed.label)
            self.notifier.notify_recovery(zone_id, prev)
            self._record(status, "RECOVERED", [])
        else:
            log.debug("[%s] 유지: %s", zone_id, confirmed.label)

    def _record(self, status: ZoneStatus, etype: str, actions: list[str]) -> None:
        top = max(status.domain_results.values(), key=lambda r: r.level)
        self.influx.write_anomaly_event(
            zone_id=status.zone_id, domain=top.domain.value,
            level=status.zone_level.label, event_type=etype,
            triggered_sensors=top.triggered_sensors, sensor_values=top.sensor_values)

    # ── 단회 실행 ─────────────────────────────────────────────────
    def run_once(self) -> None:
        zones = self.pg.get_active_zones()
        log.info("단회 평가: %d개 구역", len(zones))
        for z in zones:
            zid = z["zone_id"]
            try:
                status    = self.evaluate_zone(zid)
                confirmed = self._debounce_level(zid, status)
                self._handle(status, confirmed)
            except Exception as e:
                log.error("[%s] 오류: %s", zid, e)

    # ── 메인 루프 ─────────────────────────────────────────────────
    def run(self) -> None:
        log.info("=" * 60)
        log.info("지하공동구 이상감지 엔진 시작")
        log.info("=" * 60)

        poll = settings.poll
        last: dict[str, float] = {k: 0.0 for k in
                                   ["fire_gas","flood","condensation","structure"]}
        domain_cache: dict[str, dict[DetectionDomain, DomainResult]] = {}

        while not self._stop.is_set():
            now   = time.time()
            zones = self.pg.get_active_zones()

            for z in zones:
                zid     = z["zone_id"]
                partial: dict[DetectionDomain, DomainResult] = {}
                updated = False

                schedule = [
                    ("fire_gas",     poll.fire_gas_sec,     DetectionDomain.FIRE_GAS),
                    ("flood",        poll.flood_sec,         DetectionDomain.FLOOD),
                    ("condensation", poll.condensation_sec,  DetectionDomain.CONDENSATION),
                    ("structure",    poll.structure_sec,     DetectionDomain.STRUCTURE),
                ]
                for key, interval, dom in schedule:
                    if now - last[key] >= interval:
                        partial[dom] = self.engines[dom]._safe_evaluate(zid)
                        updated = True

                if not updated:
                    continue

                # 이전 캐시와 머지
                cache = domain_cache.setdefault(zid, {})
                cache.update(partial)
                all_results = {dom: cache.get(dom, DomainResult(zone_id=zid, domain=dom,
                                                                 level=AlertLevel.NONE))
                               for dom in DetectionDomain}

                zone_level = max((r.level for r in all_results.values()), default=AlertLevel.NONE)
                status = ZoneStatus(zone_id=zid, zone_level=zone_level,
                                    domain_results=all_results,
                                    previous_level=self._prev.get(zid, AlertLevel.NONE))
                confirmed = self._debounce_level(zid, status)
                self._handle(status, confirmed)

            for key, interval, _ in schedule:
                if now - last[key] >= interval:
                    last[key] = now

            time.sleep(1)

    def stop(self) -> None:
        log.info("종료 신호 수신")
        self._stop.set()

    def close(self) -> None:
        self.influx.close()
        self.pg.close()
        log.info("리소스 해제 완료")

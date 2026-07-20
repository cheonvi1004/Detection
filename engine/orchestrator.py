"""
engine/orchestrator.py
───────────────────────
4개 엔진 통합 오케스트레이터 + 메인 루프.

설계서 4.1절 기준:
- 화재/가스·침수·결로·구조 병렬 평가 (ThreadPoolExecutor)
- 구역 대표 레벨 = 4개 중 최고값
- Debounce: L1·L2=30초, L3=10초, L4=즉시
- 레벨 상향 → 알림 발송 + 이벤트 기록
- 레벨 하향 → 복구 알림
- 도메인별 폴링 주기 독립 관리 + 이전 결과 캐싱
"""
from __future__ import annotations
import time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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
    candidate: AlertLevel       = AlertLevel.NONE
    since:     Optional[float]  = None


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

        # resource_id별 상태 추적
        self._prev:     dict[str, AlertLevel]  = {}
        self._debounce: dict[str, _Debounce]   = {}
        # 도메인별 이전 평가 결과 캐시 (폴링 주기가 다른 도메인 결과 보존)
        self._cache:    dict[str, dict[DetectionDomain, DomainResult]] = {}
        self._stop      = threading.Event()

    # ── 단일 구역 전체 평가 ───────────────────────────────────────
    def evaluate_zone(self, resource_id: str) -> ZoneStatus:
        """4개 영역 ThreadPoolExecutor 병렬 평가."""
        results: dict[DetectionDomain, DomainResult] = {}
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix=f"eval-{resource_id}") as exe:
            futs = {exe.submit(eng._safe_evaluate, resource_id): dom
                    for dom, eng in self.engines.items()}
            for f in as_completed(futs):
                dom = futs[f]
                try:
                    results[dom] = f.result()
                except Exception as e:
                    log.error("[%s] %s 예외: %s", resource_id, dom.value, e)
                    results[dom] = DomainResult(
                        resource_id=resource_id, domain=dom,
                        level=AlertLevel.NONE, detail=str(e)
                    )
        zone_level = max((r.level for r in results.values()), default=AlertLevel.NONE)
        return ZoneStatus(
            resource_id=resource_id, zone_level=zone_level,
            domain_results=results,
            previous_level=self._prev.get(resource_id, AlertLevel.NONE),
        )

    # ── Debounce ─────────────────────────────────────────────────
    def _apply_debounce(self, resource_id: str, new: AlertLevel) -> AlertLevel:
        prev = self._prev.get(resource_id, AlertLevel.NONE)
        if new <= prev:
            self._debounce.pop(resource_id, None)
            return new

        wait = settings.debounce.get(new.value)
        if wait == 0:
            self._debounce.pop(resource_id, None)
            return new

        now   = time.time()
        state = self._debounce.get(resource_id)
        if state is None or state.candidate != new:
            self._debounce[resource_id] = _Debounce(candidate=new, since=now)
            log.debug("[%s] debounce 시작: %s→%s (%ds)",
                      resource_id, prev.label, new.label, wait)
            return prev

        elapsed = now - state.since
        if elapsed >= wait:
            self._debounce.pop(resource_id, None)
            log.info("[%s] debounce 완료(%.1fs): %s→%s",
                     resource_id, elapsed, prev.label, new.label)
            return new

        log.debug("[%s] debounce 대기 %.1f/%.0fs", resource_id, elapsed, wait)
        return prev

    # ── 상태 변화 처리 ────────────────────────────────────────────
    def _handle(self, status: ZoneStatus, confirmed: AlertLevel) -> None:
        rid  = status.resource_id
        prev = self._prev.get(rid, AlertLevel.NONE)
        self._prev[rid]   = confirmed
        status.zone_level = confirmed

        if confirmed > prev:
            etype = "ESCALATED" if prev > AlertLevel.NONE else "TRIGGERED"
            log.warning("⬆ [%s] %s → %s (%s)", rid, prev.label, confirmed.label, etype)
            actions = self.notifier.notify(status)
            self._record(status, etype, actions)

        elif confirmed < prev:
            log.info("⬇ [%s] %s → %s (RECOVERED)", rid, prev.label, confirmed.label)
            self.notifier.notify_recovery(rid, prev)
            self._record(status, "RECOVERED", [])

        else:
            if confirmed > AlertLevel.NONE:
                log.debug("[%s] 유지: %s", rid, confirmed.label)

    def _record(self, status: ZoneStatus, etype: str, actions: list[str]) -> None:
        top = max(status.domain_results.values(), key=lambda r: r.level)
        self.influx.write_anomaly_event(
            resource_id=status.resource_id,
            domain=top.domain.value,
            level=status.zone_level.label,
            event_type=etype,
            triggered_sensors=top.triggered_sensors,
            sensor_values={**top.sensor_values, "actions": ",".join(actions)},
        )

    # ── 단회 실행 (테스트·CI용) ───────────────────────────────────
    def run_once(self) -> None:
        resources = self.pg.get_active_resources()
        log.info("단회 평가: %d개 구역", len(resources))
        for r in resources:
            rid = r["resource_id"]
            try:
                status    = self.evaluate_zone(rid)
                confirmed = self._apply_debounce(rid, status.zone_level)
                self._handle(status, confirmed)
            except Exception as e:
                log.error("[%s] 평가 오류: %s", rid, e)

    # ── 메인 루프 ─────────────────────────────────────────────────
    def run(self) -> None:
        log.info("=" * 60)
        log.info("지하공동구 이상감지 엔진 시작")
        log.info("=" * 60)

        poll = settings.poll
        _schedule = [
            ("fire_gas",     poll.fire_gas_sec,     DetectionDomain.FIRE_GAS),
            ("flood",        poll.flood_sec,         DetectionDomain.FLOOD),
            ("condensation", poll.condensation_sec,  DetectionDomain.CONDENSATION),
            ("structure",    poll.structure_sec,     DetectionDomain.STRUCTURE),
        ]
        last_run: dict[str, float] = {k: 0.0 for k, _, _ in _schedule}

        while not self._stop.is_set():
            now       = time.time()
            resources = self.pg.get_active_resources()

            # 이번 사이클에 실행할 도메인 목록
            due_domains = {
                dom for key, interval, dom in _schedule
                if now - last_run[key] >= interval
            }

            if due_domains:
                for r in resources:
                    rid = r["resource_id"]
                    cache = self._cache.setdefault(rid, {})
                    updated = False

                    for dom in due_domains:
                        result = self.engines[dom]._safe_evaluate(rid)
                        cache[dom] = result
                        updated    = True

                    if not updated:
                        continue

                    # 캐시에 없는 도메인은 NONE으로 채움
                    all_results = {
                        dom: cache.get(
                            dom,
                            DomainResult(resource_id=rid, domain=dom, level=AlertLevel.NONE)
                        )
                        for dom in DetectionDomain
                    }
                    zone_level = max(
                        (r.level for r in all_results.values()),
                        default=AlertLevel.NONE
                    )
                    status = ZoneStatus(
                        resource_id=rid, zone_level=zone_level,
                        domain_results=all_results,
                        previous_level=self._prev.get(rid, AlertLevel.NONE),
                    )
                    confirmed = self._apply_debounce(rid, zone_level)
                    self._handle(status, confirmed)

                # 실행한 도메인의 last_run 갱신
                for key, interval, dom in _schedule:
                    if dom in due_domains:
                        last_run[key] = now

            time.sleep(1)

    def stop(self) -> None:
        log.info("종료 신호 수신")
        self._stop.set()

    def close(self) -> None:
        self.influx.close()
        self.pg.close()
        log.info("리소스 해제 완료")

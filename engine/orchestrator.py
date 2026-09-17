"""
engine/orchestrator.py
───────────────────────
4개 엔진 통합 오케스트레이터 + 메인 루프.

변경 사항:
- 각 도메인(센서)별 독립 평가 및 즉시 API 알림(POST) 전송 체계로 개편
- 디바운스(Debounce) 로직 제거: 상태 유지는 중계 API에 위임하고 매 주기별 상태 즉시 전달
- 통합 평가(ZoneStatus) 제거 및 엔진별 빠른 반응성 확보
"""
from __future__ import annotations
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.settings import settings
from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult
from engine.base import BaseDetectionEngine
from engine.fire_gas import FireGasEngine
from engine.flood import FloodEngine
from engine.condensation import CondensationEngine
from engine.structure import StructureEngine

# 변경된 알림 모듈 임포트
from alert.notifier import EventNotifier
from infra.influx_client import InfluxRepo
from infra.pg_client import PgRepo
from utils.logger import get_logger

log = get_logger(__name__)

class Orchestrator:
    def __init__(self) -> None:
        self.influx   = InfluxRepo()
        self.pg       = PgRepo()
        
        # API POST 전송을 담당하는 Notifier로 교체
        self.notifier = EventNotifier()
        self.pg.connect()

        self.engines: dict[DetectionDomain, BaseDetectionEngine] = {
            DetectionDomain.FIRE_GAS:     FireGasEngine(self.influx, self.pg),
            DetectionDomain.FLOOD:        FloodEngine(self.influx, self.pg),
            DetectionDomain.CONDENSATION: CondensationEngine(self.influx, self.pg),
            DetectionDomain.STRUCTURE:    StructureEngine(self.influx, self.pg),
        }

        # 상태 변경(이벤트) 감지용 이전 상태 저장소
        self._prev: dict[str, dict[DetectionDomain, AlertLevel]] = {}
        self._stop = threading.Event()

    # ── 상태 변화 처리 (디바운스 없이 즉각 발송) ───────────────────────────
    def _handle_domain(self, result: DomainResult) -> None:
        rid = getattr(result, "resource_id", getattr(result, "resource_id", "UNKNOWN"))
        dom = result.domain
        current_level = result.level
        
        if rid not in self._prev:
            self._prev[rid] = {}
            
        prev = self._prev[rid].get(dom, AlertLevel.NONE)
        self._prev[rid][dom] = current_level

        log.warning("current_level: %s, rid: %s, dom: %s, prev: %s, current: %s", current_level, rid, dom.name, prev.label, current_level.label)

        # 💡 [추가] 엔진이 판별한 상세 사유와 문제 센서 데이터 추출
        reason = result.detail
        # 프로젝트 버전에 따라 속성명이 다를 수 있어 안전하게 가져옵니다
        bad_sensors = getattr(result, "sensor_results", getattr(result, "triggered_sensors", []))
        
        # 💡 위험 상태(주의 단계 이상) 감지 시: API는 무조건 전송 (API 서버에서 카운팅)
        if current_level > AlertLevel.NONE:
            # DB 로깅용 상태 정의
            if current_level > prev:
                etype = "ESCALATED" if prev > AlertLevel.NONE else "TRIGGERED"
                #log.warning("🚨 [%s][%s] %s → %s (%s)", rid, dom.name, prev.label, current_level.label, etype)
                # 💡 여기에 reason과 bad_sensors를 출력하도록 추가했습니다!
                log.warning("🚨 [%s][%s] %s → %s (%s) | 사유: %s | 원인 센서 데이터: %s", 
                            rid, dom.name, prev.label, current_level.label, etype, reason, bad_sensors)
            elif current_level < prev:
                etype = "DOWNGRADED"
                log.warning("⬇ [%s][%s] %s → %s (%s)", rid, dom.name, prev.label, current_level.label, etype)
            else:
                etype = "MAINTAINED"
            
            # 1. 이벤트 API POST 발송 (매번 발송)
            self.notifier.send_alert(result)
            
            # 2. InfluxDB 이력 기록 (상태가 변했을 때만 DB에 기록)
            if etype != "MAINTAINED":
                self._record_domain(result, etype)

        # 위험 단계 완전히 하향(복구) 시
        elif current_level == AlertLevel.NONE and prev > AlertLevel.NONE:
            log.info("✅ [%s][%s] %s → %s (RECOVERED)", rid, dom.name, prev.label, current_level.label)
            self._record_domain(result, "RECOVERED")

    def _record_domain(self, result: DomainResult, etype: str) -> None:
        """InfluxDB에 개별 도메인 단위의 이벤트(히스토리)를 기록합니다."""
        rid = getattr(result, "resource_id", getattr(result, "resource_id", "UNKNOWN"))
        self.influx.write_anomaly_event(
            resource_id=rid,
            domain=result.domain.value,
            level=result.level.label,
            event_type=etype,
            triggered_sensors=result.triggered_sensors,
            sensor_values=result.sensor_values,
        )

    # ── 단회 실행 (테스트·CI용) ───────────────────────────────────
    def run_once(self) -> None:
        resources = self.pg.get_active_resources()
        log.info("단회 평가: %d개 구역", len(resources))
        
        for r in resources:
            rid = r["resource_id"]
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix=f"eval-{rid}") as exe:
                futs = {exe.submit(eng._safe_evaluate, rid): dom for dom, eng in self.engines.items()}
                for f in as_completed(futs):
                    dom = futs[f]
                    try:
                        result = f.result()
                        # 디바운스 거치지 않고 바로 결과 처리
                        self._handle_domain(result)
                    except Exception as e:
                        log.error("[%s] %s 평가 오류: %s", rid, dom.name, e)

    # ── 메인 루프 (도메인별 독립 폴링) ────────────────────
    def run(self) -> None:
        log.info("=" * 60)
        log.info("지하공동구 이상감지 엔진 시작 (도메인별 독립 평가 및 즉시 API 알림)")
        log.info("=" * 60)

        poll = settings.poll
        _schedule = [
            ("fire_gas",     poll.fire_gas_sec,     DetectionDomain.FIRE_GAS),
            ("flood",        poll.flood_sec,        DetectionDomain.FLOOD),
            ("condensation", poll.condensation_sec, DetectionDomain.CONDENSATION),
            ("structure",    poll.structure_sec,    DetectionDomain.STRUCTURE)
        ]
        last_run: dict[str, float] = {k: 0.0 for k, _, _ in _schedule}

        while not self._stop.is_set():
            now       = time.time()
            resources = self.pg.get_active_resources()

            due_domains = {
                dom for key, interval, dom in _schedule
                if now - last_run[key] >= interval
            }

            if due_domains:
                for r in resources:
                    rid = r["resource_id"]

                    for dom in due_domains:
                        try:
                            # 1. 도메인별 개별 평가 수행
                            result = self.engines[dom]._safe_evaluate(rid)

                            # 2. 도메인별 상태 처리 및 즉각 알림(API) 발송 (디바운스 제거)
                            self._handle_domain(result)
                        except Exception as e:
                            log.error("[%s] %s 평가 중 예기치 않은 오류 발생: %s", rid, dom.name, e)

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
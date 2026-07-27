"""
engine/flood.py
────────────────
침수/수위 감지 엔진.

최종 DDL 반영:
- anomaly_flood_parameters에서 resource_id 기준 물리 파라미터 조회
- drain_disabled_margin_pct: DB 저장값 그대로 사용
  (L4 기준 = pump_total_capacity_lpm × (1 + margin/100))
- inlet_pipe_height_mm NULL → 수위 기반 감지 불가 구역 처리
"""
from __future__ import annotations
from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult
from engine.base import BaseDetectionEngine
from config.settings import settings


class FloodEngine(BaseDetectionEngine):
    domain = DetectionDomain.FLOOD

    def evaluate(self, resource_id: str) -> DomainResult:
        # ── DB에서 구역별 물리 파라미터 조회 ─────────────────────
        cfg = self.pg.get_flood_config(resource_id)
        if cfg is None:
            return self._missing(resource_id, "anomaly_flood_parameters")

        if not cfg.is_configurable:
            self.log.warning(
                "[%s] 유입관 높이 미설정 — 수위 감지 불가 (note: %s)",
                resource_id, cfg.note or "없음"
            )
            return DomainResult(
                resource_id=resource_id, domain=self.domain,
                level=AlertLevel.NONE,
                detail=f"수위 감지 불가: inlet_pipe_height_mm 미설정",
            )

        if not cfg.is_verified:
            self.log.warning("[%s] 미검증 파라미터 사용 중", resource_id)

        # ── InfluxDB 센서값 조회 ──────────────────────────────────
        data    = self.influx.get_flood_data(resource_id)
        water   = data.get("water_level")
        inflow  = data.get("inflow_rate")
        drain   = data.get("drain_rate")
        rise    = data.get("rise_rate")

        sv: dict[str, float] = {}
        if water  is not None: sv["water_level"]  = water
        if inflow is not None: sv["inflow_rate"]  = inflow
        if drain  is not None: sv["drain_rate"]   = drain

        # ── L4: 배수불능 ─────────────────────────────────────────
        # 조건: 유입량 > pump_total_capacity_lpm × (1 + margin/100)
        if (inflow is not None
                and cfg.level4_inflow_threshold_lpm is not None
                and inflow > cfg.level4_inflow_threshold_lpm):
            return DomainResult(
                resource_id=resource_id, domain=self.domain,
                level=AlertLevel.LEVEL_4,
                triggered_sensors=["FLOW_INFLOW"], sensor_values=sv,
                detail=(
                    f"배수불능: 유입량 {inflow:.1f} L/min > "
                    f"기준 {cfg.level4_inflow_threshold_lpm:.1f} L/min "
                    f"(margin {cfg.drain_disabled_margin_pct:.0f}%)"
                ),
            )

        # ── L3: 수위 유입관+offset 초과 ──────────────────────────
        if (water is not None
                and cfg.level3_trigger_mm is not None
                and water >= cfg.level3_trigger_mm):
            return DomainResult(
                resource_id=resource_id, domain=self.domain,
                level=AlertLevel.LEVEL_3,
                triggered_sensors=["WATER_LEVEL"], sensor_values=sv,
                detail=(
                    f"수위 경계: {water:.1f} mm ≥ "
                    f"유입관+{cfg.level3_offset_mm:.0f}mm "
                    f"({cfg.level3_trigger_mm:.1f} mm)"
                ),
            )

        # ── L2: 수위 유입관 높이 도달 ────────────────────────────
        if (water is not None
                and cfg.level2_trigger_mm is not None
                and water >= cfg.level2_trigger_mm):
            return DomainResult(
                resource_id=resource_id, domain=self.domain,
                level=AlertLevel.LEVEL_2,
                triggered_sensors=["WATER_LEVEL"], sensor_values=sv,
                detail=(
                    f"수위 주의: {water:.1f} mm ≥ "
                    f"유입관 높이 {cfg.level2_trigger_mm:.1f} mm"
                ),
            )

        # ── L1: 수위 급상승 ──────────────────────────────────────
        if (rise is not None
                and rise >= settings.rapid_rise_threshold_mm_per_min):
            return DomainResult(
                resource_id=resource_id, domain=self.domain,
                level=AlertLevel.LEVEL_1,
                triggered_sensors=["WATER_LEVEL"], sensor_values=sv,
                detail=f"수위 급상승: {rise:.1f} mm/min",
            )

        return DomainResult(
            resource_id=resource_id, domain=self.domain,
            level=AlertLevel.NONE, sensor_values=sv, detail="정상",
        )

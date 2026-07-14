"""engine/flood.py — 침수 감지 엔진 (DB 파라미터 조회)"""
from __future__ import annotations
from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult
from engine.base import BaseDetectionEngine
from config.settings import settings


class FloodEngine(BaseDetectionEngine):
    domain = DetectionDomain.FLOOD

    def evaluate(self, zone_id: str) -> DomainResult:
        cfg = self.pg.get_flood_config(zone_id)
        if cfg is None:
            return self._missing_sensor(zone_id, "flood_zone_parameters")

        if not cfg.is_configurable:
            self.log.warning("[%s] 유입관 높이 미설정 — 감지 스킵: %s", zone_id, cfg.config_status)
            return DomainResult(zone_id=zone_id, domain=self.domain,
                                level=AlertLevel.NONE,
                                detail=f"수위 감지 불가: {cfg.config_status}")

        if not cfg.is_verified:
            self.log.warning("[%s] 미검증 파라미터: %s", zone_id, cfg.config_status)

        data = self.influx.get_flood_data(zone_id)
        water = data.get("water_level")
        inflow = data.get("inflow_rate")
        rise   = data.get("rise_rate")

        sv: dict[str, float] = {}
        if water  is not None: sv["water_level"]  = water
        if inflow is not None: sv["inflow_rate"]  = inflow
        if data.get("drain_rate") is not None: sv["drain_rate"] = data["drain_rate"]

        # L4 배수불능
        if (inflow is not None and cfg.level4_inflow_threshold_lpm is not None
                and inflow > cfg.level4_inflow_threshold_lpm):
            return DomainResult(zone_id=zone_id, domain=self.domain,
                                level=AlertLevel.LEVEL_4,
                                triggered_sensors=["FLOW_INFLOW"], sensor_values=sv,
                                detail=f"배수불능: {inflow:.1f} L/min > {cfg.level4_inflow_threshold_lpm:.1f}")

        # L3 수위 경계
        if (water is not None and cfg.level3_trigger_mm is not None
                and water >= cfg.level3_trigger_mm):
            return DomainResult(zone_id=zone_id, domain=self.domain,
                                level=AlertLevel.LEVEL_3,
                                triggered_sensors=["WATER_LEVEL"], sensor_values=sv,
                                detail=f"수위 경계: {water:.1f} mm ≥ {cfg.level3_trigger_mm:.1f} mm")

        # L2 수위 주의
        if (water is not None and cfg.level2_trigger_mm is not None
                and water >= cfg.level2_trigger_mm):
            return DomainResult(zone_id=zone_id, domain=self.domain,
                                level=AlertLevel.LEVEL_2,
                                triggered_sensors=["WATER_LEVEL"], sensor_values=sv,
                                detail=f"수위 주의: {water:.1f} mm ≥ {cfg.level2_trigger_mm:.1f} mm")

        # L1 급상승
        if rise is not None and rise >= settings.rapid_rise_threshold_mm_per_min:
            return DomainResult(zone_id=zone_id, domain=self.domain,
                                level=AlertLevel.LEVEL_1,
                                triggered_sensors=["WATER_LEVEL"], sensor_values=sv,
                                detail=f"수위 급상승: {rise:.1f} mm/min")

        return DomainResult(zone_id=zone_id, domain=self.domain,
                            level=AlertLevel.NONE, sensor_values=sv, detail="정상")

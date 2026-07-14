"""engine/flood.py — 침수 감지 엔진"""
from __future__ import annotations

from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult
from engine.base import BaseDetectionEngine
from config.settings import settings


class FloodEngine(BaseDetectionEngine):

    domain = DetectionDomain.FLOOD

    # ───────────────────────────────────────
    # mm → Liter 환산
    # ───────────────────────────────────────
    @staticmethod
    def _liter_per_mm(cfg) -> float:
        """
        집수정 높이 1mm당 용량 계산
        """

        if not cfg.sump_height_mm:
            return 0.0

        if cfg.sump_height_mm <= 0:
            return 0.0

        return cfg.sump_capacity_liters / cfg.sump_height_mm

    # ───────────────────────────────────────
    # 추정 유입량 계산
    # rise_rate(mm/min) → L/min
    # ───────────────────────────────────────
    @classmethod
    def _estimate_inflow_lpm(cls, rise_rate: float, cfg) -> float:

        if rise_rate is None or rise_rate <= 0:
            return 0.0

        return rise_rate * cls._liter_per_mm(cfg)

    # ───────────────────────────────────────
    # DomainResult 생성 helper
    # ───────────────────────────────────────
    def _result(
        self,
        zone_id: str,
        level: AlertLevel,
        detail: str,
        sensors: list[str],
        sensor_values: dict
    ) -> DomainResult:

        return DomainResult(
            zone_id=zone_id,
            domain=self.domain,
            level=level,
            triggered_sensors=sensors,
            sensor_values=sensor_values,
            detail=detail
        )

    # ───────────────────────────────────────
    # 메인 평가
    # ───────────────────────────────────────
    def evaluate(self, zone_id: str) -> DomainResult:

        cfg = self.pg.get_flood_config(zone_id)

        # 설정 없음
        if cfg is None:
            return self._missing_sensor(
                zone_id,
                "flood_resource_parameters"
            )

        # 필수 설정 미완료
        if not cfg.is_configurable:

            self.log.warning(
                "[%s] 침수 설정 미완료 — 감지 스킵: %s",
                zone_id,
                cfg.config_status
            )

            return self._result(
                zone_id=zone_id,
                level=AlertLevel.NONE,
                detail=f"침수 감지 불가: {cfg.config_status}",
                sensors=[],
                sensor_values={}
            )

        # 미검증 경고
        if not cfg.is_verified:

            self.log.warning(
                "[%s] 미검증 침수 파라미터: %s",
                zone_id,
                cfg.config_status
            )

        # ───────────────────────────────────
        # 센서 데이터 조회
        # ───────────────────────────────────
        data = self.influx.get_flood_data(zone_id)

        water_level = data.get("water_level")      # mm
        rise_rate   = data.get("rise_rate")        # mm/min
        pump_on     = data.get("pump_on", False)

        # ───────────────────────────────────
        # 센서값 저장
        # ───────────────────────────────────
        sv: dict[str, float] = {}

        if water_level is not None:
            sv["water_level_mm"] = water_level

        if rise_rate is not None:
            sv["rise_rate_mm_per_min"] = rise_rate

        # ───────────────────────────────────
        # 추정 유입량 계산
        # ───────────────────────────────────
        estimated_inflow_lpm = None

        if rise_rate is not None and rise_rate > 0:

            estimated_inflow_lpm = self._estimate_inflow_lpm(
                rise_rate,
                cfg
            )

            sv["estimated_inflow_lpm"] = round(
                estimated_inflow_lpm,
                2
            )

        # ───────────────────────────────────
        # LEVEL 4 — 배수불능
        #
        # 조건:
        # - 펌프 동작 중
        # - 추정 유입량 > 펌프 배수능력
        # ───────────────────────────────────
        if (
            pump_on
            and estimated_inflow_lpm is not None
            and cfg.pump_total_capacity_lpm
        ):

            threshold = (
                cfg.pump_total_capacity_lpm *
                (
                    1 +
                    (cfg.drain_disabled_margin_pct / 100.0)
                )
            )

            if estimated_inflow_lpm > threshold:

                return self._result(
                    zone_id=zone_id,
                    level=AlertLevel.LEVEL_4,
                    detail=(
                        f"배수불능 추정: "
                        f"유입량 {estimated_inflow_lpm:.1f} L/min > "
                        f"배수능력 {threshold:.1f} L/min"
                    ),
                    sensors=["WATER_LEVEL"],
                    sensor_values=sv
                )

        # ───────────────────────────────────
        # LEVEL 3 — 위험 수위
        # ───────────────────────────────────
        if (
            water_level is not None
            and cfg.level3_trigger_mm is not None
            and water_level >= cfg.level3_trigger_mm
        ):

            return self._result(
                zone_id=zone_id,
                level=AlertLevel.LEVEL_3,
                detail=(
                    f"위험 수위: "
                    f"{water_level:.1f} mm ≥ "
                    f"{cfg.level3_trigger_mm:.1f} mm"
                ),
                sensors=["WATER_LEVEL"],
                sensor_values=sv
            )

        # ───────────────────────────────────
        # LEVEL 2 — 유입관 도달
        # ───────────────────────────────────
        if (
            water_level is not None
            and cfg.level2_trigger_mm is not None
            and water_level >= cfg.level2_trigger_mm
        ):

            return self._result(
                zone_id=zone_id,
                level=AlertLevel.LEVEL_2,
                detail=(
                    f"유입관 도달: "
                    f"{water_level:.1f} mm ≥ "
                    f"{cfg.level2_trigger_mm:.1f} mm"
                ),
                sensors=["WATER_LEVEL"],
                sensor_values=sv
            )

        # ───────────────────────────────────
        # LEVEL 1 — 급상승
        # ───────────────────────────────────
        if (
            rise_rate is not None
            and rise_rate >= settings.rapid_rise_threshold_mm_per_min
        ):

            return self._result(
                zone_id=zone_id,
                level=AlertLevel.LEVEL_1,
                detail=f"수위 급상승: {rise_rate:.1f} mm/min",
                sensors=["WATER_LEVEL"],
                sensor_values=sv
            )

        # 정상
        return self._result(
            zone_id=zone_id,
            level=AlertLevel.NONE,
            detail="정상",
            sensors=[],
            sensor_values=sv
        )
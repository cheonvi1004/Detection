"""
engine/condensation.py — 결로 감지 엔진 (Murray 1967 Magnus-Tetens, ΔT 3단계)

공식:
  es(T)  = a × exp( b×T / (c+T) )       포화수증기압 (Pa)
  e      = (RH/100) × es                 실제 수증기압 (Pa)
  T_dew  = c × ln(e/a) / (b − ln(e/a))  노점온도 (℃)
  ΔT     = T_dry − T_dew
"""
from __future__ import annotations
import math
from typing import Optional

from domain.enums import AlertLevel, DetectionDomain, Season
from domain.models import CondensationConfig, DomainResult
from engine.base import BaseDetectionEngine


class CondensationEngine(BaseDetectionEngine):
    domain = DetectionDomain.CONDENSATION

    def evaluate(self, zone_id: str) -> DomainResult:
        cfg = self.pg.get_condensation_config(zone_id)
        if cfg is None:
            return self._missing_sensor(zone_id, "condensation_config")

        data   = self.influx.get_condensation_data(zone_id)
        t_dry  = data.get("wall_temp")
        t_ext  = data.get("ext_temperature")
        rh     = data.get("humidity")

        if t_dry is None: return self._missing_sensor(zone_id, "wall_temp")
        if rh    is None: return self._missing_sensor(zone_id, "humidity")

        t_dew   = self._dew_point(t_dry, rh, cfg)
        delta_t = t_dry - t_dew

        sv = {"wall_temp": t_dry, "humidity": rh,
              "dew_point": round(t_dew, 2), "delta_T": round(delta_t, 2)}
        if t_ext is not None:
            sv["ext_temperature"] = t_ext
            sv["season"] = self._season(t_ext, cfg).value

        level, detail = self._level(delta_t, cfg)

        if level >= AlertLevel.LEVEL_3 and not cfg.has_ventilation:
            self.log.warning("[%s] 결로 경계 — 환기설비 미보유, 수동 대응 필요", zone_id)

        self.log.debug("[%s] 결로: T_dry=%.1f  T_dew=%.2f  ΔT=%.2f → %s",
                       zone_id, t_dry, t_dew, delta_t, level.label)
        return DomainResult(zone_id=zone_id, domain=self.domain,
                            level=self._cap_level(level),
                            triggered_sensors=["WALL_TEMP","EXT_HUMIDITY"] if level > AlertLevel.NONE else [],
                            sensor_values=sv, detail=detail)

    # ── Magnus-Tetens ────────────────────────────────────────────
    @staticmethod
    def _dew_point(t_dry: float, rh: float, cfg: CondensationConfig) -> float:
        """Murray(1967) Magnus-Tetens 노점온도 계산."""
        a, b, c = cfg.coeff_a, cfg.coeff_b, cfg.coeff_c
        es = a * math.exp(b * t_dry / (c + t_dry))   # 포화수증기압 (Pa)
        e  = (rh / 100.0) * es                         # 실제 수증기압 (Pa)
        if e <= 0:
            return t_dry
        ln_r  = math.log(e / a)
        return c * ln_r / (b - ln_r)                   # 노점온도 (℃)

    @staticmethod
    def _level(delta_t: float, cfg: CondensationConfig) -> tuple[AlertLevel, str]:
        if delta_t <= 0:
            return AlertLevel.LEVEL_3, f"결로 발생: ΔT={delta_t:.2f}℃ ≤ 0℃"
        if delta_t <= cfg.level2_delta_t:
            return AlertLevel.LEVEL_2, f"결로 주의: ΔT={delta_t:.2f}℃ ≤ Y={cfg.level2_delta_t}℃"
        if delta_t <= cfg.level1_delta_t:
            return AlertLevel.LEVEL_1, f"결로 관심: ΔT={delta_t:.2f}℃ ≤ X={cfg.level1_delta_t}℃"
        return AlertLevel.NONE, f"정상: ΔT={delta_t:.2f}℃"

    @staticmethod
    def _season(t_ext: float, cfg: CondensationConfig) -> Season:
        if t_ext <= cfg.season_winter_max_c: return Season.WINTER
        if t_ext <= cfg.season_spring_max_c: return Season.SPRING_FALL
        return Season.SUMMER

    def get_ventilation_target(self, zone_id: str, t_ext: float) -> Optional[dict]:
        cfg = self.pg.get_condensation_config(zone_id)
        if not cfg: return None
        s = self._season(t_ext, cfg)
        return {Season.WINTER:     {"mode":"winter",     "target_temp_c":cfg.target_temp_winter, "target_rh_pct":cfg.target_rh_winter},
                Season.SPRING_FALL:{"mode":"spring_fall","target_temp_c":cfg.target_temp_spring, "target_rh_pct":cfg.target_rh_spring},
                Season.SUMMER:     {"mode":"summer",     "target_temp_c":cfg.target_temp_summer, "target_rh_pct":cfg.target_rh_summer}}[s]

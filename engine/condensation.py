"""
engine/condensation.py
───────────────────────
결로 감지 엔진.

최종 DDL 반영:
- anomaly_condensation_thresholds: level2_delta_t 만 존재
  → level1_delta_t는 CondensationConfig.level1_delta_t property(Y×5/3)로 자동 산출
- 단계 판정:
    경계(L3): ΔT ≤ 0        → 결로 발생 (고정)
    주의(L2): ΔT ≤ Y        → level2_delta_t
    관심(L1): ΔT ≤ X        → level1_delta_t (= Y × 5/3)
    정상:     ΔT >  X
- 최대 단계: LEVEL_3 (심각 없음)

공식 (Murray, 1967):
  es = a × exp(b×T / (c+T))
  e  = (RH/100) × es
  Td = c × ln(e/a) / (b − ln(e/a))
  ΔT = T_dry − Td
"""
from __future__ import annotations
import math
from typing import Optional

from domain.enums import AlertLevel, DetectionDomain, Season
from domain.models import CondensationConfig, DomainResult
from engine.base import BaseDetectionEngine


class CondensationEngine(BaseDetectionEngine):
    domain = DetectionDomain.CONDENSATION

    def evaluate(self, resource_id: str) -> DomainResult:
        # ── DB에서 결로 설정 조회 ─────────────────────────────────
        cfg = self.pg.get_condensation_config(resource_id)
        if cfg is None:
            return self._missing(resource_id, "condensation_config")

        # ── InfluxDB 센서값 조회 ──────────────────────────────────
        data  = self.influx.get_condensation_data(resource_id)
        t_dry = data.get("wall_temp")        # 건구온도 (벽체온도, ℃)
        t_ext = data.get("ext_temperature")  # 외기온도 (℃)
        rh    = data.get("humidity")         # 상대습도 (%)

        if t_dry is None: return self._missing(resource_id, "wall_temp")
        if rh    is None: return self._missing(resource_id, "humidity")

        # ── Magnus-Tetens 노점온도 산출 ───────────────────────────
        t_dew   = self._dew_point(t_dry, rh, cfg)
        delta_t = t_dry - t_dew

        sv: dict[str, float] = {
            "wall_temp":  t_dry,
            "humidity":   rh,
            "dew_point":  round(t_dew,   2),
            "delta_T":    round(delta_t, 2),
            # 참고용: DB 설정값
            "level1_delta_t": cfg.level1_delta_t,
            "level2_delta_t": cfg.level2_delta_t,
        }
        if t_ext is not None:
            sv["ext_temperature"] = t_ext
            sv["season"] = self._season(t_ext, cfg).value

        # ── 단계 판정 ─────────────────────────────────────────────
        level, detail = self._level(delta_t, cfg)
        capped        = self._cap(level)

        if capped >= AlertLevel.LEVEL_3 and not cfg.has_ventilation:
            self.log.warning(
                "[%s] 결로 경계 감지 — 환기설비 미보유, 수동 대응 필요", resource_id
            )

        self.log.debug(
            "[%s] 결로: T_dry=%.1f  T_dew=%.2f  ΔT=%.2f(L1≤%.1f, L2≤%.1f) → %s",
            resource_id, t_dry, t_dew, delta_t,
            cfg.level1_delta_t, cfg.level2_delta_t, capped.label,
        )
        return DomainResult(
            resource_id=resource_id, domain=self.domain, level=capped,
            triggered_sensors=["WALL_TEMP", "EXT_HUMIDITY"] if capped > AlertLevel.NONE else [],
            sensor_values=sv, detail=detail,
        )

    # ── Magnus-Tetens 공식 ────────────────────────────────────────
    @staticmethod
    def _dew_point(t_dry: float, rh: float, cfg: CondensationConfig) -> float:
        """
        Murray(1967) Magnus-Tetens 공식.
        es(T) = a × exp(b×T / (c+T))
        e     = (RH/100) × es
        Td    = c × ln(e/a) / (b − ln(e/a))
        """
        a, b, c = cfg.coeff_a, cfg.coeff_b, cfg.coeff_c
        es = a * math.exp(b * t_dry / (c + t_dry))
        e  = (rh / 100.0) * es
        if e <= 0:
            return t_dry  # 방어 코드
        ln_r = math.log(e / a)
        return c * ln_r / (b - ln_r)

    @staticmethod
    def _level(delta_t: float, cfg: CondensationConfig) -> tuple[AlertLevel, str]:
        """
        ΔT 기반 단계 판정.
        L3: ΔT ≤ 0 (결로 발생, 고정)
        L2: ΔT ≤ Y (level2_delta_t, DB 설정)
        L1: ΔT ≤ X (level1_delta_t = Y × 5/3, 자동 산출)
        """
        if delta_t <= 0:
            return AlertLevel.LEVEL_3, f"결로 발생: ΔT={delta_t:.2f}℃ ≤ 0℃"
        if delta_t <= cfg.level2_delta_t:
            return AlertLevel.LEVEL_2, (
                f"결로 주의: ΔT={delta_t:.2f}℃ ≤ Y={cfg.level2_delta_t}℃"
            )
        if delta_t <= cfg.level1_delta_t:
            return AlertLevel.LEVEL_1, (
                f"결로 관심: ΔT={delta_t:.2f}℃ ≤ X={cfg.level1_delta_t}℃ "
                f"(Y×5/3={cfg.level2_delta_t}×5/3)"
            )
        return AlertLevel.NONE, f"정상: ΔT={delta_t:.2f}℃ > X={cfg.level1_delta_t}℃"

    @staticmethod
    def _season(t_ext: float, cfg: CondensationConfig) -> Season:
        if t_ext <= cfg.season_winter_max_c: return Season.WINTER
        if t_ext <= cfg.season_spring_max_c: return Season.SPRING_FALL
        return Season.SUMMER

    def get_ventilation_target(self, resource_id: str, t_ext: float) -> Optional[dict]:
        """계절별 환기 목표 환경 반환 (BAS 제어 명령 생성 시 사용)."""
        cfg = self.pg.get_condensation_config(resource_id)
        if not cfg: return None
        s = self._season(t_ext, cfg)
        return {
            Season.WINTER:     {"mode": "winter",
                                "target_temp_c": cfg.target_temp_winter,
                                "target_rh_pct": cfg.target_rh_winter},
            Season.SPRING_FALL:{"mode": "spring_fall",
                                "target_temp_c": cfg.target_temp_spring,
                                "target_rh_pct": cfg.target_rh_spring},
            Season.SUMMER:     {"mode": "summer",
                                "target_temp_c": cfg.target_temp_summer,
                                "target_rh_pct": cfg.target_rh_summer},
        }[s]

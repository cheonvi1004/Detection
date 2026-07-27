"""
engine/condensation.py
───────────────────────
결로 감지 엔진 — 다중 센서 Worst-case 평가.

v2.1 핵심 변경:
  1. DB에서 해당 구역에 등록된 센서 ID 목록 조회
     (anomaly_sensors 테이블 → pg_client.get_condensation_sensors)
  2. InfluxDB에서 센서 목록을 한 번의 쿼리로 일괄 조회
     (get_condensation_data_multi)
  3. 센서별 ΔT(= T_dry − T_dew) 개별 계산
  4. ΔT가 가장 작은(= 결로 위험이 가장 높은) 센서를 Worst-case로 선정
  5. Worst-case 센서의 단계를 구역 대표 레벨로 채택
  6. 테이블 미존재 시 단일 센서 폴백 모드 자동 전환

공식 (Murray, 1967 Magnus-Tetens):
  es = a × exp(b×T / (c+T))
  e  = (RH/100) × es
  Td = c × ln(e/a) / (b − ln(e/a))
  ΔT = T_dry − Td

단계 판정:
  L3 (경계): ΔT ≤ 0          → 결로 발생 (고정)
  L2 (주의): ΔT ≤ Y          → level2_delta_t (DB 설정)
  L1 (관심): ΔT ≤ X          → level1_delta_t (= Y × 5/3, 자동 산출)
  정상:      ΔT >  X
  최대 단계: LEVEL_3 (심각 없음)
"""
from __future__ import annotations
import math
from typing import Optional

from domain.enums import AlertLevel, DetectionDomain, Season
from domain.models import (
    CondensationConfig, CondensationSensorResult, DomainResult, SensorMeta
)
from engine.base import BaseDetectionEngine


class CondensationEngine(BaseDetectionEngine):
    domain = DetectionDomain.CONDENSATION

    def evaluate(self, resource_id: str) -> DomainResult:
        # ── 1. DB에서 결로 설정 조회 ─────────────────────────────
        cfg = self.pg.get_condensation_config(resource_id)
        if cfg is None:
            return self._missing(resource_id, "condensation_config")

        # ── 2. DB에서 센서 목록 조회 ─────────────────────────────
        sensors: list[SensorMeta] = self.pg.get_condensation_sensors(resource_id)

        if sensors:
            return self._evaluate_multi(resource_id, cfg, sensors)
        else:
            # anomaly_sensors 미존재 또는 미등록 → 단일 센서 폴백
            self.log.debug(
                "[%s] 등록된 결로 센서 없음 — 단일 센서 폴백 모드", resource_id
            )
            return self._evaluate_single(resource_id, cfg)

    # ── 다중 센서 평가 (메인 경로) ───────────────────────────────
    def _evaluate_multi(
        self,
        resource_id: str,
        cfg: CondensationConfig,
        sensors: list[SensorMeta],
    ) -> DomainResult:
        """
        센서 목록 기반 다중 평가.
        wall_temp + humidity 쌍이 모두 있는 sensor_id만 유효 센서로 처리.
        """
        # 유효한 wall_temp 센서 ID 추출 (humidity는 공용일 수 있으므로 별도 처리)
        wall_temp_ids = [s.sensor_id for s in sensors if s.sensor_type == "wall_temp"]
        humidity_ids  = [s.sensor_id for s in sensors if s.sensor_type == "humidity"]
        ext_temp_ids  = [s.sensor_id for s in sensors if s.sensor_type == "ext_temperature"]

        all_sensor_ids = list({*wall_temp_ids, *humidity_ids, *ext_temp_ids})

        if not all_sensor_ids:
            self.log.warning("[%s] wall_temp/humidity 센서 미등록", resource_id)
            return self._evaluate_single(resource_id, cfg)

        # ── 3. InfluxDB 일괄 조회 ────────────────────────────────
        raw: dict[str, dict] = self.influx.get_condensation_data_multi(
            resource_id, all_sensor_ids
        )

        # ── 4. 센서별 ΔT 계산 ────────────────────────────────────
        sensor_results: list[CondensationSensorResult] = []

        # wall_temp 센서 기준으로 순회
        # humidity가 같은 sensor_id에 없으면 구역 공용 humidity 사용
        common_rh    = self._pick_common(raw, humidity_ids, "humidity")
        common_t_ext = self._pick_common(raw, ext_temp_ids, "ext_temperature")

        for sid in wall_temp_ids:
            sid_data = raw.get(sid, {})
            t_dry    = sid_data.get("wall_temp")

            # humidity: 같은 sensor_id에 있으면 우선, 없으면 공용값
            rh = sid_data.get("humidity") or common_rh
            # ext_temperature: 공용 또는 해당 센서 값
            t_ext = sid_data.get("ext_temperature") or common_t_ext

            if t_dry is None:
                self.log.warning("[%s][%s] wall_temp 결측 — 스킵", resource_id, sid)
                continue
            if rh is None:
                self.log.warning("[%s][%s] humidity 결측 — 스킵", resource_id, sid)
                continue

            t_dew   = self._dew_point(t_dry, rh, cfg)
            delta_t = t_dry - t_dew
            level, detail = self._level(delta_t, cfg)

            sensor_results.append(CondensationSensorResult(
                sensor_id=sid,
                t_dry=t_dry, rh=rh, t_dew=round(t_dew, 2),
                delta_t=round(delta_t, 2),
                level=self._cap(level),
                detail=detail,
                t_ext=t_ext,
            ))

        if not sensor_results:
            return self._missing(resource_id, "유효 결로 센서 (wall_temp+humidity 쌍)")

        # ── 5. Worst-case 선정 (ΔT 최솟값 = 결로 위험 최대) ─────
        worst = min(sensor_results, key=lambda r: r.delta_t)

        # 센서별 요약 로그
        self._log_sensor_summary(resource_id, sensor_results, worst)

        # ── 6. 구역 대표 값 조립 ─────────────────────────────────
        # sensor_values: Worst-case 값 + 전체 센서 ΔT 목록
        sv: dict[str, float] = {
            "wall_temp":        worst.t_dry,
            "humidity":         worst.rh,
            "dew_point":        worst.t_dew,
            "delta_T":          worst.delta_t,
            "level1_delta_t":   cfg.level1_delta_t,
            "level2_delta_t":   cfg.level2_delta_t,
            "sensor_count":     float(len(sensor_results)),
        }
        if worst.t_ext is not None:
            sv["ext_temperature"] = worst.t_ext
            sv["season"] = float(
                [Season.WINTER, Season.SPRING_FALL, Season.SUMMER].index(
                    self._season(worst.t_ext, cfg)
                )
            )

        # 전체 센서 ΔT를 sensor_values에 포함 (모니터링·디버깅용)
        for sr in sensor_results:
            sv[f"delta_T_{sr.sensor_id}"] = sr.delta_t

        if worst.level >= AlertLevel.LEVEL_3 and not cfg.has_ventilation:
            self.log.warning(
                "[%s] 결로 경계 — 환기설비 미보유, 수동 대응 필요", resource_id
            )

        return DomainResult(
            resource_id=resource_id,
            domain=self.domain,
            level=worst.level,
            triggered_sensors=[sr.sensor_id for sr in sensor_results
                                if sr.level > AlertLevel.NONE],
            sensor_values=sv,
            detail=(
                f"[Worst: {worst.sensor_id}] {worst.detail} "
                f"(전체 {len(sensor_results)}개 센서 중 ΔT 최솟값)"
            ),
            sensor_results=sensor_results,
        )

    # ── 단일 센서 폴백 ────────────────────────────────────────────
    def _evaluate_single(
        self, resource_id: str, cfg: CondensationConfig
    ) -> DomainResult:
        """
        anomaly_sensors 테이블 미존재 또는 센서 미등록 시 폴백.
        resource_id 태그만으로 InfluxDB 조회.
        """
        data  = self.influx.get_condensation_data(resource_id)
        t_dry = data.get("wall_temp")
        t_ext = data.get("ext_temperature")
        rh    = data.get("humidity")

        if t_dry is None:
            return self._missing(resource_id, "wall_temp")
        if rh is None:
            return self._missing(resource_id, "humidity")

        t_dew   = self._dew_point(t_dry, rh, cfg)
        delta_t = t_dry - t_dew
        level, detail = self._level(delta_t, cfg)
        capped  = self._cap(level)

        sv: dict[str, float] = {
            "wall_temp":      t_dry,
            "humidity":       rh,
            "dew_point":      round(t_dew, 2),
            "delta_T":        round(delta_t, 2),
            "level1_delta_t": cfg.level1_delta_t,
            "level2_delta_t": cfg.level2_delta_t,
        }
        if t_ext is not None:
            sv["ext_temperature"] = t_ext

        if capped >= AlertLevel.LEVEL_3 and not cfg.has_ventilation:
            self.log.warning(
                "[%s] 결로 경계 — 환기설비 미보유, 수동 대응 필요", resource_id
            )

        self.log.debug(
            "[%s][단일폴백] T_dry=%.1f  T_dew=%.2f  ΔT=%.2f → %s",
            resource_id, t_dry, t_dew, delta_t, capped.label,
        )
        return DomainResult(
            resource_id=resource_id, domain=self.domain, level=capped,
            triggered_sensors=["wall_temp"] if capped > AlertLevel.NONE else [],
            sensor_values=sv, detail=f"[단일폴백] {detail}",
        )

    # ── 공통 유틸 ─────────────────────────────────────────────────
    @staticmethod
    def _pick_common(
        raw: dict[str, dict],
        sensor_ids: list[str],
        field: str,
    ) -> Optional[float]:
        """
        여러 센서 중 field 값을 하나라도 가진 첫 번째 유효값 반환.
        외기온도·공용 습도 센서처럼 구역 전체 공용으로 쓰이는 경우 사용.
        """
        for sid in sensor_ids:
            v = raw.get(sid, {}).get(field)
            if v is not None:
                return v
        return None

    def _log_sensor_summary(
        self,
        resource_id: str,
        results: list[CondensationSensorResult],
        worst: CondensationSensorResult,
    ) -> None:
        """센서별 ΔT 요약을 DEBUG 레벨로 출력."""
        summary = ", ".join(
            f"{r.sensor_id}:ΔT={r.delta_t:.2f}({'★' if r is worst else ''})"
            for r in sorted(results, key=lambda r: r.delta_t)
        )
        self.log.debug(
            "[%s] 결로 다중센서 평가(%d개): %s → Worst=%s(%s)",
            resource_id, len(results), summary,
            worst.sensor_id, worst.level.label,
        )

    # ── Magnus-Tetens 공식 ────────────────────────────────────────
    @staticmethod
    def _dew_point(t_dry: float, rh: float, cfg: CondensationConfig) -> float:
        """
        Murray(1967) Magnus-Tetens.
        es = a × exp(b×T / (c+T))
        e  = (RH/100) × es
        Td = c × ln(e/a) / (b − ln(e/a))
        """
        a, b, c = cfg.coeff_a, cfg.coeff_b, cfg.coeff_c
        es   = a * math.exp(b * t_dry / (c + t_dry))
        e    = (rh / 100.0) * es
        if e <= 0:
            return t_dry
        ln_r = math.log(e / a)
        return c * ln_r / (b - ln_r)

    @staticmethod
    def _level(
        delta_t: float, cfg: CondensationConfig
    ) -> tuple[AlertLevel, str]:
        if delta_t <= 0:
            return (AlertLevel.LEVEL_3,
                    f"결로 발생: ΔT={delta_t:.2f}℃ ≤ 0℃")
        if delta_t <= cfg.level2_delta_t:
            return (AlertLevel.LEVEL_2,
                    f"결로 주의: ΔT={delta_t:.2f}℃ ≤ Y={cfg.level2_delta_t}℃")
        if delta_t <= cfg.level1_delta_t:
            return (AlertLevel.LEVEL_1,
                    f"결로 관심: ΔT={delta_t:.2f}℃ ≤ X={cfg.level1_delta_t}℃")
        return (AlertLevel.NONE,
                f"정상: ΔT={delta_t:.2f}℃ > X={cfg.level1_delta_t}℃")

    @staticmethod
    def _season(t_ext: float, cfg: CondensationConfig) -> Season:
        if t_ext <= cfg.season_winter_max_c: return Season.WINTER
        if t_ext <= cfg.season_spring_max_c: return Season.SPRING_FALL
        return Season.SUMMER

    def get_ventilation_target(
        self, resource_id: str, t_ext: float
    ) -> Optional[dict]:
        """계절별 환기 목표 환경 반환."""
        cfg = self.pg.get_condensation_config(resource_id)
        if not cfg:
            return None
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

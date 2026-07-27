"""
engine/fire_gas.py
───────────────────
화재/가스 감지 엔진.

최종 DDL 반영:
- anomaly_thresholds에서 sensor_category='FIRE_GAS' 행을 동적으로 조회
- composite_group / composite_min_count / composite_target_level 로직 적용
  예) 동일 composite_group의 임계값이 composite_min_count개 이상 충족 시
      composite_target_level로 강제 상향
"""
from __future__ import annotations
from collections import defaultdict
from typing import Optional

from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult, ThresholdRow
from engine.base import BaseDetectionEngine

# sensor_element_type → InfluxDB _field 매핑
_FIELD: dict[str, str] = {
    "TEMP_RATE":     "temp_rate",
    "TEMP_ABSOLUTE": "temperature",
    "GAS_O2":        "O2",
    "GAS_CO":        "CO",
    "GAS_CO2":       "CO2",
    "GAS_H2S":       "H2S",
}


class FireGasEngine(BaseDetectionEngine):
    domain = DetectionDomain.FIRE_GAS

    def evaluate(self, resource_id: str) -> DomainResult:
        # ── DB에서 임계값 동적 조회 ──────────────────────────────
        rows = self.pg.get_thresholds(resource_id, "FIRE_GAS")
        if not rows:
            return self._missing(resource_id, "FIRE_GAS thresholds")

        # ── InfluxDB 센서값 조회 ──────────────────────────────────
        data = self.influx.get_fire_gas_data(resource_id)

        # ── 센서별 단계 평가 ─────────────────────────────────────
        sensor_values: dict[str, float] = {}
        sensor_levels: dict[str, AlertLevel] = {}

        # sensor_element_type별로 임계값 그룹핑 (내림차순 정렬 보장)
        by_type: dict[str, list[ThresholdRow]] = defaultdict(list)
        for row in rows:
            by_type[row.sensor_element_type].append(row)
        # 각 타입 내 레벨 내림차순 정렬 (높은 레벨부터 먼저 검사)
        for stype in by_type:
            by_type[stype].sort(key=lambda r: r.alert_level, reverse=True)

        for stype, threshold_rows in by_type.items():
            field = _FIELD.get(stype)
            if field is None:
                self.log.warning("[%s] 알 수 없는 sensor_element_type: %s", resource_id, stype)
                continue

            raw = data.get(field)
            if raw is None:
                self.log.warning("[%s] 센서 결측: %s", resource_id, stype)
                continue

            sensor_values[stype] = raw
            sensor_levels[stype] = self._eval_sensor(raw, threshold_rows)

        if not sensor_levels:
            return self._missing(resource_id, "ALL sensors")

        # ── composite 조건 처리 ───────────────────────────────────
        final_level = self._apply_composite(rows, sensor_levels)

        triggered = [s for s, lv in sensor_levels.items() if lv >= AlertLevel.LEVEL_1]
        top       = [s for s, lv in sensor_levels.items() if lv == final_level]
        detail    = f"트리거: {', '.join(top)}" if top and final_level > AlertLevel.NONE else "정상"

        self.log.debug("[%s] 화재/가스: %s", resource_id, final_level.label)
        return DomainResult(
            resource_id=resource_id, domain=self.domain, level=final_level,
            triggered_sensors=triggered, sensor_values=sensor_values, detail=detail,
        )

    @staticmethod
    def _eval_sensor(value: float, rows: list[ThresholdRow]) -> AlertLevel:
        """단일 센서에 대해 레벨 내림차순 규칙 순서대로 적용."""
        for row in rows:
            if row.operator.evaluate(value, row.threshold_value):
                return row.alert_level
        return AlertLevel.NONE

    @staticmethod
    def _apply_composite(
        all_rows: list[ThresholdRow],
        sensor_levels: dict[str, AlertLevel],
    ) -> AlertLevel:
        """
        composite_group이 있는 임계값 행들을 그룹별로 묶어
        composite_min_count 이상 충족 시 composite_target_level로 상향.
        그룹 없으면 단순 max.
        """
        base_level = max(sensor_levels.values(), default=AlertLevel.NONE)

        # composite_group이 있는 행만 추출
        group_rows: dict[str, list[ThresholdRow]] = defaultdict(list)
        for row in all_rows:
            if row.composite_group and row.composite_target_level:
                group_rows[row.composite_group].append(row)

        if not group_rows:
            return base_level

        composite_level = AlertLevel.NONE
        for group, g_rows in group_rows.items():
            # 이 그룹의 최소 충족 수
            min_count = g_rows[0].composite_min_count if g_rows else 1
            target    = g_rows[0].composite_target_level

            # 해당 그룹 임계값을 충족한 센서 수
            satisfied = sum(
                1 for row in g_rows
                if row.sensor_element_type in sensor_levels
                and row.operator.evaluate(
                    sensor_levels.get(row.sensor_element_type, AlertLevel.NONE).value,
                    row.alert_level.value
                )
            )
            if satisfied >= min_count and target:
                composite_level = max(composite_level, target)

        return max(base_level, composite_level)

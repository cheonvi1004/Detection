"""
engine/structure.py
────────────────────
구조 안전 감지 엔진.

최종 DDL 반영:
- anomaly_thresholds sensor_category='STRUCTURE' 동적 조회
- composite_group / composite_min_count / composite_target_level 로직 적용
  예) CRACK_WIDTH + VIBRATION 동시 L3 → composite_target_level=LEVEL_4
- 진동(VIBRATION): MAX 집계, 균열/변형: MEAN 집계 (DB aggregation 컬럼 기준)
"""
from __future__ import annotations
from collections import defaultdict

from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult, ThresholdRow
from engine.base import BaseDetectionEngine

# sensor_element_type → InfluxDB _field 매핑
_FIELD: dict[str, str] = {
    "CRACK_WIDTH": "crack_width",
    "STRAIN":      "strain",
    "VIBRATION":   "vibration",
}


class StructureEngine(BaseDetectionEngine):
    domain = DetectionDomain.STRUCTURE

    def evaluate(self, resource_id: str) -> DomainResult:
        # ── DB에서 임계값 동적 조회 ──────────────────────────────
        rows = self.pg.get_thresholds(resource_id, "STRUCTURE")
        if not rows:
            return self._missing(resource_id, "STRUCTURE thresholds")

        # ── InfluxDB 센서값 조회 ──────────────────────────────────
        data = self.influx.get_structure_data(resource_id)

        # ── 센서별 단계 평가 ─────────────────────────────────────
        sv:  dict[str, float]     = {}
        sl:  dict[str, AlertLevel] = {}

        by_type: dict[str, list[ThresholdRow]] = defaultdict(list)
        for row in rows:
            by_type[row.sensor_element_type].append(row)
        for stype in by_type:
            by_type[stype].sort(key=lambda r: r.alert_level, reverse=True)

        for stype, t_rows in by_type.items():
            field = _FIELD.get(stype)
            if field is None:
                continue
            raw = data.get(field)
            if raw is None:
                self.log.warning("[%s] 센서 결측: %s", resource_id, stype)
                continue
            sv[stype] = raw
            sl[stype] = self._eval_sensor(raw, t_rows)

        if not sl:
            return self._missing(resource_id, "ALL sensors")

        # ── composite 조건 처리 ───────────────────────────────────
        final_level = self._apply_composite(rows, sl)

        triggered = [s for s, lv in sl.items() if lv >= AlertLevel.LEVEL_1]
        detail    = self._build_detail(sl, sv)

        self.log.debug("[%s] 구조: %s", resource_id, final_level.label)
        return DomainResult(
            resource_id=resource_id, domain=self.domain, level=final_level,
            triggered_sensors=triggered, sensor_values=sv, detail=detail,
        )

    @staticmethod
    def _eval_sensor(value: float, rows: list[ThresholdRow]) -> AlertLevel:
        for row in rows:
            if row.operator.evaluate(value, row.threshold_value):
                return row.alert_level
        return AlertLevel.NONE

    @staticmethod
    def _apply_composite(
        all_rows: list[ThresholdRow],
        sensor_levels: dict[str, AlertLevel],
    ) -> AlertLevel:
        """composite_group 기반 복합 조건 상향 처리."""
        base = max(sensor_levels.values(), default=AlertLevel.NONE)

        group_rows: dict[str, list[ThresholdRow]] = defaultdict(list)
        for row in all_rows:
            if row.composite_group and row.composite_target_level:
                group_rows[row.composite_group].append(row)

        if not group_rows:
            return base

        composite = AlertLevel.NONE
        for _, g_rows in group_rows.items():
            min_count = g_rows[0].composite_min_count
            target    = g_rows[0].composite_target_level
            satisfied = sum(
                1 for row in g_rows
                if sensor_levels.get(row.sensor_element_type, AlertLevel.NONE) >= row.alert_level
            )
            if satisfied >= min_count and target:
                composite = max(composite, target)

        return max(base, composite)

    @staticmethod
    def _build_detail(
        sl: dict[str, AlertLevel], sv: dict[str, float]
    ) -> str:
        units = {"CRACK_WIDTH": "mm", "STRAIN": "με", "VIBRATION": "cm/s"}
        parts = [
            f"{s}={sv.get(s, '?')}{units.get(s, '')}({lv.label})"
            for s, lv in sl.items() if lv > AlertLevel.NONE
        ]
        return ", ".join(parts) if parts else "정상"

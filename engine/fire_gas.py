"""engine/fire_gas.py — 화재/가스 감지 엔진"""
from __future__ import annotations
from domain.enums import AlertLevel, DetectionDomain, ThresholdOp
from domain.models import DomainResult
from engine.base import BaseDetectionEngine

_RULES: dict[str, list[tuple[AlertLevel, ThresholdOp, float]]] = {
    "TEMP_RATE":     [(AlertLevel.LEVEL_2, ThresholdOp.GTE, 8.0)],
    "TEMP_ABSOLUTE": [(AlertLevel.LEVEL_4, ThresholdOp.GTE, 75.0),
                      (AlertLevel.LEVEL_3, ThresholdOp.GTE, 60.0)],
    "GAS_O2":        [(AlertLevel.LEVEL_4, ThresholdOp.LTE, 8.0),
                      (AlertLevel.LEVEL_3, ThresholdOp.LTE, 10.0),
                      (AlertLevel.LEVEL_2, ThresholdOp.LTE, 15.0)],
    "GAS_CO":        [(AlertLevel.LEVEL_4, ThresholdOp.GTE, 2500.0),
                      (AlertLevel.LEVEL_3, ThresholdOp.GTE, 2000.0),
                      (AlertLevel.LEVEL_2, ThresholdOp.GTE, 1400.0)],
    "GAS_CO2":       [(AlertLevel.LEVEL_4, ThresholdOp.GTE, 30.0),
                      (AlertLevel.LEVEL_3, ThresholdOp.GTE, 10.0),
                      (AlertLevel.LEVEL_2, ThresholdOp.GTE, 5.0)],
    "GAS_H2S":       [(AlertLevel.LEVEL_4, ThresholdOp.GTE, 500.0),
                      (AlertLevel.LEVEL_3, ThresholdOp.GTE, 200.0),
                      (AlertLevel.LEVEL_2, ThresholdOp.GTE, 100.0)],
}
_FIELD = {"TEMP_RATE":"temp_rate","TEMP_ABSOLUTE":"temperature",
           "GAS_O2":"O2","GAS_CO":"CO","GAS_CO2":"CO2","GAS_H2S":"H2S"}


class FireGasEngine(BaseDetectionEngine):
    domain = DetectionDomain.FIRE_GAS

    def evaluate(self, zone_id: str) -> DomainResult:
        data = self.influx.get_fire_gas_data(zone_id)
        sensor_values: dict[str, float] = {}
        sensor_levels: dict[str, AlertLevel] = {}

        for stype, rules in _RULES.items():
            raw = data.get(_FIELD[stype])
            if raw is None:
                self.log.warning("[%s] 센서 결측: %s", zone_id, stype)
                continue
            sensor_values[stype] = raw
            sensor_levels[stype] = self._eval_sensor(raw, rules)

        if not sensor_levels:
            return self._missing_sensor(zone_id, "ALL")

        triggered_l3 = [s for s,lv in sensor_levels.items() if lv >= AlertLevel.LEVEL_3]
        triggered_l2 = [s for s,lv in sensor_levels.items() if lv >= AlertLevel.LEVEL_2]

        if len(triggered_l3) >= 2:
            final = AlertLevel.LEVEL_4
            detail = f"복합조건 L3→L4 ({', '.join(triggered_l3)})"
        elif len(triggered_l2) >= 2:
            final  = max(max(sensor_levels.values()), AlertLevel.LEVEL_3)
            detail = f"복합조건 L2→L3 ({', '.join(triggered_l2)})"
        else:
            final  = max(sensor_levels.values())
            top    = [s for s,lv in sensor_levels.items() if lv == final]
            detail = f"트리거: {', '.join(top)}" if top else "정상"

        triggered = [s for s,lv in sensor_levels.items() if lv >= AlertLevel.LEVEL_1]
        self.log.debug("[%s] 화재/가스: %s", zone_id, final.label)
        return DomainResult(zone_id=zone_id, domain=self.domain, level=final,
                            triggered_sensors=triggered, sensor_values=sensor_values, detail=detail)

    @staticmethod
    def _eval_sensor(value: float, rules) -> AlertLevel:
        for level, op, threshold in rules:
            if op.evaluate(value, threshold):
                return level
        return AlertLevel.NONE

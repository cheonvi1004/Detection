"""tests/test_fire_gas.py — 화재/가스 엔진 단위 테스트 (DDL v2)"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel, ThresholdOp, AggregationFn
from domain.models import ThresholdRow
from engine.fire_gas import FireGasEngine


def _row(stype, level, op, val, group=None, min_count=1, target=None):
    return ThresholdRow(
        resource_id="TEST", sensor_category="FIRE_GAS",
        sensor_element_type=stype, alert_level=level,
        operator=op, threshold_value=val,
        aggregation=AggregationFn.LAST, eval_window_sec=60,
        composite_group=group, composite_min_count=min_count,
        composite_target_level=target,
        description=None, is_active=True,
    )


class TestEvalSensor(unittest.TestCase):
    def test_no_match_returns_none(self):
        rows = [_row("TEMP_RATE", AlertLevel.LEVEL_2, ThresholdOp.GTE, 8.0)]
        self.assertEqual(FireGasEngine._eval_sensor(5.0, rows), AlertLevel.NONE)

    def test_temp_rate_l2(self):
        rows = [_row("TEMP_RATE", AlertLevel.LEVEL_2, ThresholdOp.GTE, 8.0)]
        self.assertEqual(FireGasEngine._eval_sensor(8.0,  rows), AlertLevel.LEVEL_2)
        self.assertEqual(FireGasEngine._eval_sensor(10.0, rows), AlertLevel.LEVEL_2)

    def test_abs_temp_multi_level(self):
        rows = [
            _row("TEMP_ABSOLUTE", AlertLevel.LEVEL_4, ThresholdOp.GTE, 75.0),
            _row("TEMP_ABSOLUTE", AlertLevel.LEVEL_3, ThresholdOp.GTE, 60.0),
        ]
        self.assertEqual(FireGasEngine._eval_sensor(55.0, rows), AlertLevel.NONE)
        self.assertEqual(FireGasEngine._eval_sensor(65.0, rows), AlertLevel.LEVEL_3)
        self.assertEqual(FireGasEngine._eval_sensor(80.0, rows), AlertLevel.LEVEL_4)

    def test_o2_lte(self):
        rows = [
            _row("GAS_O2", AlertLevel.LEVEL_4, ThresholdOp.LTE, 8.0),
            _row("GAS_O2", AlertLevel.LEVEL_3, ThresholdOp.LTE, 10.0),
            _row("GAS_O2", AlertLevel.LEVEL_2, ThresholdOp.LTE, 15.0),
        ]
        self.assertEqual(FireGasEngine._eval_sensor(20.0, rows), AlertLevel.NONE)
        self.assertEqual(FireGasEngine._eval_sensor(14.0, rows), AlertLevel.LEVEL_2)
        self.assertEqual(FireGasEngine._eval_sensor(9.0,  rows), AlertLevel.LEVEL_3)
        self.assertEqual(FireGasEngine._eval_sensor(7.0,  rows), AlertLevel.LEVEL_4)


class TestComposite(unittest.TestCase):
    def test_no_composite_returns_max(self):
        rows = [
            _row("TEMP_ABSOLUTE", AlertLevel.LEVEL_3, ThresholdOp.GTE, 60.0),
        ]
        sl = {"TEMP_ABSOLUTE": AlertLevel.LEVEL_3}
        result = FireGasEngine._apply_composite(rows, sl)
        self.assertEqual(result, AlertLevel.LEVEL_3)

    def test_composite_2sensors_l3_promotes_l4(self):
        rows = [
            _row("TEMP_ABSOLUTE", AlertLevel.LEVEL_3, ThresholdOp.GTE, 60.0,
                 group="G1", min_count=2, target=AlertLevel.LEVEL_4),
            _row("GAS_CO", AlertLevel.LEVEL_3, ThresholdOp.GTE, 2000.0,
                 group="G1", min_count=2, target=AlertLevel.LEVEL_4),
        ]
        sl = {
            "TEMP_ABSOLUTE": AlertLevel.LEVEL_3,
            "GAS_CO":        AlertLevel.LEVEL_3,
        }
        result = FireGasEngine._apply_composite(rows, sl)
        self.assertEqual(result, AlertLevel.LEVEL_4)

    def test_composite_not_enough_sensors(self):
        rows = [
            _row("TEMP_ABSOLUTE", AlertLevel.LEVEL_3, ThresholdOp.GTE, 60.0,
                 group="G1", min_count=2, target=AlertLevel.LEVEL_4),
            _row("GAS_CO", AlertLevel.LEVEL_3, ThresholdOp.GTE, 2000.0,
                 group="G1", min_count=2, target=AlertLevel.LEVEL_4),
        ]
        # TEMP_ABSOLUTE만 L3 충족, GAS_CO는 L2 → 복합 조건 미충족
        sl = {
            "TEMP_ABSOLUTE": AlertLevel.LEVEL_3,
            "GAS_CO":        AlertLevel.LEVEL_2,
        }
        result = FireGasEngine._apply_composite(rows, sl)
        self.assertEqual(result, AlertLevel.LEVEL_3)  # base_level


class TestThresholdOp(unittest.TestCase):
    def test_all_ops(self):
        self.assertTrue(ThresholdOp.GTE.evaluate(5.0, 5.0))
        self.assertTrue(ThresholdOp.LTE.evaluate(4.0, 5.0))
        self.assertTrue(ThresholdOp.GT.evaluate(5.1,  5.0))
        self.assertTrue(ThresholdOp.LT.evaluate(4.9,  5.0))
        self.assertFalse(ThresholdOp.GTE.evaluate(4.9, 5.0))
        self.assertFalse(ThresholdOp.GT.evaluate(5.0,  5.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)

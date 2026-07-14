"""tests/test_fire_gas.py — 화재/가스 엔진 단위 테스트"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel, ThresholdOp
from engine.fire_gas import FireGasEngine


class TestFireGasEvalSensor(unittest.TestCase):
    def test_none_if_no_match(self):
        rules = [(AlertLevel.LEVEL_2, ThresholdOp.GTE, 8.0)]
        self.assertEqual(FireGasEngine._eval_sensor(5.0, rules), AlertLevel.NONE)

    def test_l2_temp_rate(self):
        rules = [(AlertLevel.LEVEL_2, ThresholdOp.GTE, 8.0)]
        self.assertEqual(FireGasEngine._eval_sensor(8.0, rules),  AlertLevel.LEVEL_2)
        self.assertEqual(FireGasEngine._eval_sensor(10.0, rules), AlertLevel.LEVEL_2)

    def test_abs_temp_levels(self):
        rules = [(AlertLevel.LEVEL_4, ThresholdOp.GTE, 75.0),
                 (AlertLevel.LEVEL_3, ThresholdOp.GTE, 60.0)]
        self.assertEqual(FireGasEngine._eval_sensor(55.0, rules), AlertLevel.NONE)
        self.assertEqual(FireGasEngine._eval_sensor(65.0, rules), AlertLevel.LEVEL_3)
        self.assertEqual(FireGasEngine._eval_sensor(80.0, rules), AlertLevel.LEVEL_4)

    def test_o2_lte(self):
        rules = [(AlertLevel.LEVEL_4, ThresholdOp.LTE, 8.0),
                 (AlertLevel.LEVEL_3, ThresholdOp.LTE, 10.0),
                 (AlertLevel.LEVEL_2, ThresholdOp.LTE, 15.0)]
        self.assertEqual(FireGasEngine._eval_sensor(20.0, rules), AlertLevel.NONE)
        self.assertEqual(FireGasEngine._eval_sensor(14.0, rules), AlertLevel.LEVEL_2)
        self.assertEqual(FireGasEngine._eval_sensor(9.0, rules),  AlertLevel.LEVEL_3)
        self.assertEqual(FireGasEngine._eval_sensor(7.0, rules),  AlertLevel.LEVEL_4)

    def test_co_thresholds(self):
        rules = [(AlertLevel.LEVEL_4, ThresholdOp.GTE, 2500.0),
                 (AlertLevel.LEVEL_3, ThresholdOp.GTE, 2000.0),
                 (AlertLevel.LEVEL_2, ThresholdOp.GTE, 1400.0)]
        self.assertEqual(FireGasEngine._eval_sensor(1000.0, rules), AlertLevel.NONE)
        self.assertEqual(FireGasEngine._eval_sensor(1500.0, rules), AlertLevel.LEVEL_2)
        self.assertEqual(FireGasEngine._eval_sensor(2100.0, rules), AlertLevel.LEVEL_3)
        self.assertEqual(FireGasEngine._eval_sensor(2600.0, rules), AlertLevel.LEVEL_4)


class TestThresholdOp(unittest.TestCase):
    def test_ops(self):
        self.assertTrue(ThresholdOp.GTE.evaluate(5.0, 5.0))
        self.assertTrue(ThresholdOp.LTE.evaluate(4.0, 5.0))
        self.assertTrue(ThresholdOp.GT.evaluate(5.1, 5.0))
        self.assertTrue(ThresholdOp.LT.evaluate(4.9, 5.0))
        self.assertFalse(ThresholdOp.GTE.evaluate(4.9, 5.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""tests/test_structure.py — 구조 안전 엔진 단위 테스트 (DDL v2)"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel, ThresholdOp, AggregationFn
from domain.models import ThresholdRow
from engine.structure import StructureEngine


def _row(stype, level, op, val, group=None, min_count=1, target=None):
    return ThresholdRow(
        resource_id="TEST", sensor_category="STRUCTURE",
        sensor_element_type=stype, alert_level=level,
        operator=op, threshold_value=val,
        aggregation=AggregationFn.MEAN, eval_window_sec=300,
        composite_group=group, composite_min_count=min_count,
        composite_target_level=target,
        description=None, is_active=True,
    )


# 기본 임계값 규칙 세트
_CRACK_ROWS = [
    _row("CRACK_WIDTH", AlertLevel.LEVEL_4, ThresholdOp.GTE, 0.5),
    _row("CRACK_WIDTH", AlertLevel.LEVEL_3, ThresholdOp.GTE, 0.3),
    _row("CRACK_WIDTH", AlertLevel.LEVEL_2, ThresholdOp.GTE, 0.1),
]
_STRAIN_ROWS = [
    _row("STRAIN", AlertLevel.LEVEL_4, ThresholdOp.GT, 3000.0),
    _row("STRAIN", AlertLevel.LEVEL_3, ThresholdOp.GT, 2500.0),
    _row("STRAIN", AlertLevel.LEVEL_2, ThresholdOp.GT, 2000.0),
]
_VIB_ROWS = [
    _row("VIBRATION", AlertLevel.LEVEL_4, ThresholdOp.GTE, 1.0),
    _row("VIBRATION", AlertLevel.LEVEL_3, ThresholdOp.GTE, 0.5),
    _row("VIBRATION", AlertLevel.LEVEL_2, ThresholdOp.GTE, 0.2),
]


class TestStructureEvalSensor(unittest.TestCase):
    def test_crack_none(self):
        self.assertEqual(StructureEngine._eval_sensor(0.05, _CRACK_ROWS), AlertLevel.NONE)

    def test_crack_l2(self):
        self.assertEqual(StructureEngine._eval_sensor(0.1,  _CRACK_ROWS), AlertLevel.LEVEL_2)

    def test_crack_l3(self):
        self.assertEqual(StructureEngine._eval_sensor(0.35, _CRACK_ROWS), AlertLevel.LEVEL_3)

    def test_crack_l4(self):
        self.assertEqual(StructureEngine._eval_sensor(0.5,  _CRACK_ROWS), AlertLevel.LEVEL_4)

    def test_strain_boundary_gt(self):
        """GT 연산자: 2000 초과부터 L2"""
        self.assertEqual(StructureEngine._eval_sensor(2000.0, _STRAIN_ROWS), AlertLevel.NONE)
        self.assertEqual(StructureEngine._eval_sensor(2001.0, _STRAIN_ROWS), AlertLevel.LEVEL_2)
        self.assertEqual(StructureEngine._eval_sensor(2501.0, _STRAIN_ROWS), AlertLevel.LEVEL_3)
        self.assertEqual(StructureEngine._eval_sensor(3001.0, _STRAIN_ROWS), AlertLevel.LEVEL_4)

    def test_vibration_levels(self):
        self.assertEqual(StructureEngine._eval_sensor(0.1,  _VIB_ROWS), AlertLevel.NONE)
        self.assertEqual(StructureEngine._eval_sensor(0.2,  _VIB_ROWS), AlertLevel.LEVEL_2)
        self.assertEqual(StructureEngine._eval_sensor(0.5,  _VIB_ROWS), AlertLevel.LEVEL_3)
        self.assertEqual(StructureEngine._eval_sensor(1.0,  _VIB_ROWS), AlertLevel.LEVEL_4)


class TestStructureComposite(unittest.TestCase):
    def test_single_l3_no_composite(self):
        rows = _CRACK_ROWS + _VIB_ROWS
        sl = {"CRACK_WIDTH": AlertLevel.LEVEL_3, "VIBRATION": AlertLevel.LEVEL_2}
        result = StructureEngine._apply_composite(rows, sl)
        self.assertEqual(result, AlertLevel.LEVEL_3)

    def test_two_l3_with_composite_promotes_l4(self):
        """균열 L3 + 진동 L3 동시 → composite_target_level=L4 상향."""
        rows = [
            _row("CRACK_WIDTH", AlertLevel.LEVEL_3, ThresholdOp.GTE, 0.3,
                 group="STRUCT_DUAL", min_count=2, target=AlertLevel.LEVEL_4),
            _row("VIBRATION",   AlertLevel.LEVEL_3, ThresholdOp.GTE, 0.5,
                 group="STRUCT_DUAL", min_count=2, target=AlertLevel.LEVEL_4),
        ]
        sl = {"CRACK_WIDTH": AlertLevel.LEVEL_3, "VIBRATION": AlertLevel.LEVEL_3}
        result = StructureEngine._apply_composite(rows, sl)
        self.assertEqual(result, AlertLevel.LEVEL_4)

    def test_composite_not_met(self):
        """min_count=2 인데 1개만 충족 → 상향 없음."""
        rows = [
            _row("CRACK_WIDTH", AlertLevel.LEVEL_3, ThresholdOp.GTE, 0.3,
                 group="G", min_count=2, target=AlertLevel.LEVEL_4),
            _row("VIBRATION",   AlertLevel.LEVEL_3, ThresholdOp.GTE, 0.5,
                 group="G", min_count=2, target=AlertLevel.LEVEL_4),
        ]
        sl = {"CRACK_WIDTH": AlertLevel.LEVEL_3, "VIBRATION": AlertLevel.LEVEL_2}
        result = StructureEngine._apply_composite(rows, sl)
        self.assertEqual(result, AlertLevel.LEVEL_3)


class TestAlertLevelComparison(unittest.TestCase):
    def test_level_ordering(self):
        self.assertLess(AlertLevel.NONE,    AlertLevel.LEVEL_1)
        self.assertLess(AlertLevel.LEVEL_1, AlertLevel.LEVEL_2)
        self.assertLess(AlertLevel.LEVEL_2, AlertLevel.LEVEL_3)
        self.assertLess(AlertLevel.LEVEL_3, AlertLevel.LEVEL_4)

    def test_max_level(self):
        self.assertEqual(max([AlertLevel.LEVEL_2, AlertLevel.LEVEL_4, AlertLevel.LEVEL_1]),
                         AlertLevel.LEVEL_4)


if __name__ == "__main__":
    unittest.main(verbosity=2)

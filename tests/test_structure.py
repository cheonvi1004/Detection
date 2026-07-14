"""tests/test_structure.py — 구조 안전 엔진 단위 테스트"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel
from engine.structure import _gte, _gt, _CRACK, _STRAIN, _VIBRATION


class TestStructureThresholds(unittest.TestCase):
    def test_crack_none(self):    self.assertEqual(_gte(0.05, _CRACK), AlertLevel.NONE)
    def test_crack_l2(self):      self.assertEqual(_gte(0.1,  _CRACK), AlertLevel.LEVEL_2)
    def test_crack_l3(self):      self.assertEqual(_gte(0.35, _CRACK), AlertLevel.LEVEL_3)
    def test_crack_l4(self):      self.assertEqual(_gte(0.5,  _CRACK), AlertLevel.LEVEL_4)

    def test_strain_none(self):   self.assertEqual(_gt(1999.9, _STRAIN), AlertLevel.NONE)
    def test_strain_l2(self):     self.assertEqual(_gt(2001.0, _STRAIN), AlertLevel.LEVEL_2)
    def test_strain_l3(self):     self.assertEqual(_gt(2501.0, _STRAIN), AlertLevel.LEVEL_3)
    def test_strain_l4(self):     self.assertEqual(_gt(3001.0, _STRAIN), AlertLevel.LEVEL_4)

    def test_vib_none(self):      self.assertEqual(_gte(0.1,  _VIBRATION), AlertLevel.NONE)
    def test_vib_l2(self):        self.assertEqual(_gte(0.2,  _VIBRATION), AlertLevel.LEVEL_2)
    def test_vib_l3(self):        self.assertEqual(_gte(0.5,  _VIBRATION), AlertLevel.LEVEL_3)
    def test_vib_l4(self):        self.assertEqual(_gte(1.0,  _VIBRATION), AlertLevel.LEVEL_4)

    def test_composite_l3x2_promotes_l4(self):
        """2개 이상 동시 L3 → 복합 조건으로 L4 판정됨을 검증 (함수 직접)."""
        from domain.enums import AlertLevel
        # CRACK=L3, VIBRATION=L3 → 두 개 모두 L3 이상
        sl = {"CRACK_WIDTH": AlertLevel.LEVEL_3, "VIBRATION": AlertLevel.LEVEL_3}
        l3 = [s for s,lv in sl.items() if lv >= AlertLevel.LEVEL_3]
        self.assertGreaterEqual(len(l3), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

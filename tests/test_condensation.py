"""
tests/test_condensation.py
───────────────────────────
결로 엔진 단위 테스트.

최종 DDL 반영:
- level2_delta_t만 DB에 존재
- level1_delta_t = level2_delta_t × 5/3 (property 자동 산출)
- 최대 단계 LEVEL_3 (심각 없음)
"""
import math, sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel, DetectionDomain
from domain.models import CondensationConfig
from engine.condensation import CondensationEngine

def _cfg(level2=3.0) -> CondensationConfig:
    return CondensationConfig(
        resource_id="TEST",
        coeff_a=610.78, coeff_b=17.2694, coeff_c=237.29,
        level2_delta_t=level2,
        season_winter_max_c=12.0, season_spring_max_c=23.0,
        target_temp_winter=20.0, target_temp_spring=22.5, target_temp_summer=25.0,
        target_rh_winter=60.0,   target_rh_spring=65.0,  target_rh_summer=70.0,
    )


class TestMagnusTetens(unittest.TestCase):
    def _dew(self, t, rh, cfg=None):
        return CondensationEngine._dew_point(t, rh, cfg or _cfg())

    def test_rh100_dew_equals_dry(self):
        t = 25.0
        self.assertAlmostEqual(self._dew(t, 100.0), t, delta=0.05)

    def test_known_25c_60rh(self):
        """T=25℃, RH=60% → 노점 ≈ 16.7℃"""
        self.assertAlmostEqual(self._dew(25.0, 60.0), 16.7, delta=0.5)

    def test_known_20c_80rh(self):
        """T=20℃, RH=80% → 노점 ≈ 16.4℃"""
        self.assertAlmostEqual(self._dew(20.0, 80.0), 16.4, delta=0.5)

    def test_lower_rh_lower_dew(self):
        td60 = self._dew(25.0, 60.0)
        td40 = self._dew(25.0, 40.0)
        self.assertGreater(td60, td40)

    def test_delta_t_positive_normal(self):
        t = 25.0
        self.assertGreater(t - self._dew(t, 60.0), 0)

    def test_delta_t_zero_at_rh100(self):
        t = 25.0
        self.assertAlmostEqual(t - self._dew(t, 100.0), 0.0, delta=0.1)


class TestLevel1DeltaTProperty(unittest.TestCase):
    """level1_delta_t = level2_delta_t × 5/3 자동 산출 검증."""

    def test_default_y3_gives_x5(self):
        cfg = _cfg(level2=3.0)
        self.assertAlmostEqual(cfg.level1_delta_t, 5.0, delta=0.01)

    def test_y6_gives_x10(self):
        cfg = _cfg(level2=6.0)
        self.assertAlmostEqual(cfg.level1_delta_t, 10.0, delta=0.01)

    def test_level1_always_greater_than_level2(self):
        for y in [2.0, 3.0, 4.0, 5.0]:
            cfg = _cfg(level2=y)
            self.assertGreater(cfg.level1_delta_t, cfg.level2_delta_t)


class TestCondensationLevel(unittest.TestCase):
    def _level(self, delta_t, level2=3.0):
        return CondensationEngine._level(delta_t, _cfg(level2))[0]

    def test_normal(self):
        self.assertEqual(self._level(6.0), AlertLevel.NONE)

    def test_level1_at_x(self):
        cfg = _cfg(3.0)
        x = cfg.level1_delta_t  # 5.0
        self.assertEqual(self._level(x), AlertLevel.LEVEL_1)

    def test_level1_between_x_and_y(self):
        self.assertEqual(self._level(4.0), AlertLevel.LEVEL_1)

    def test_level2_at_y(self):
        self.assertEqual(self._level(3.0), AlertLevel.LEVEL_2)

    def test_level2_positive_below_y(self):
        self.assertEqual(self._level(1.5), AlertLevel.LEVEL_2)

    def test_level3_at_zero(self):
        self.assertEqual(self._level(0.0), AlertLevel.LEVEL_3)

    def test_level3_negative(self):
        self.assertEqual(self._level(-2.0), AlertLevel.LEVEL_3)

    def test_max_level_is_level3(self):
        """결로 영역 최대 단계 = LEVEL_3 (심각 없음)."""
        self.assertEqual(DetectionDomain.CONDENSATION.max_level, AlertLevel.LEVEL_3)

    def test_detail_contains_keyword(self):
        _, d = CondensationEngine._level(-0.5, _cfg())
        self.assertIn("결로 발생", d)
        _, d = CondensationEngine._level(4.0, _cfg())
        self.assertIn("결로 관심", d)
        _, d = CondensationEngine._level(2.0, _cfg())
        self.assertIn("결로 주의", d)


class TestSeason(unittest.TestCase):
    def _s(self, t): return CondensationEngine._season(t, _cfg())

    def test_winter(self):
        from domain.enums import Season
        self.assertEqual(self._s(0.0),  Season.WINTER)
        self.assertEqual(self._s(12.0), Season.WINTER)

    def test_spring_fall(self):
        from domain.enums import Season
        self.assertEqual(self._s(13.0), Season.SPRING_FALL)
        self.assertEqual(self._s(23.0), Season.SPRING_FALL)

    def test_summer(self):
        from domain.enums import Season
        self.assertEqual(self._s(24.0), Season.SUMMER)


if __name__ == "__main__":
    unittest.main(verbosity=2)

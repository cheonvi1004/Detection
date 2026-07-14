"""
tests/test_condensation.py
────────────────────────────
결로 엔진 단위 테스트.
Magnus-Tetens 공식 검증 + ΔT 단계 판정 검증.
"""
import math, sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel
from domain.models import CondensationConfig
from engine.condensation import CondensationEngine

# Murray(1967) 기본 계수
_CFG = CondensationConfig(
    zone_id="TEST",
    coeff_a=610.78, coeff_b=17.2694, coeff_c=237.29,
    level1_delta_t=5.0, level2_delta_t=3.0,
    season_winter_max_c=12.0, season_spring_max_c=23.0,
    target_temp_winter=20.0, target_temp_spring=22.5, target_temp_summer=25.0,
    target_rh_winter=60.0,   target_rh_spring=65.0,  target_rh_summer=70.0,
    has_ventilation=True,
)


class TestMagnusTetens(unittest.TestCase):
    """Murray(1967) 공식 정확도 검증."""

    def _dew(self, t, rh):
        return CondensationEngine._dew_point(t, rh, _CFG)

    def test_rh100_dew_equals_dry(self):
        """상대습도 100% → 노점온도 = 건구온도."""
        t = 25.0
        td = self._dew(t, 100.0)
        self.assertAlmostEqual(td, t, delta=0.05)

    def test_known_value_25c_60rh(self):
        """T=25℃, RH=60% → 노점온도 ≈ 16.7℃ (문헌값)."""
        td = self._dew(25.0, 60.0)
        self.assertAlmostEqual(td, 16.7, delta=0.5)

    def test_known_value_20c_80rh(self):
        """T=20℃, RH=80% → 노점온도 ≈ 16.4℃ (문헌값)."""
        td = self._dew(20.0, 80.0)
        self.assertAlmostEqual(td, 16.4, delta=0.5)

    def test_low_humidity_low_dew(self):
        """습도가 낮을수록 노점온도는 낮아야 한다."""
        td60 = self._dew(25.0, 60.0)
        td40 = self._dew(25.0, 40.0)
        self.assertGreater(td60, td40)

    def test_delta_t_positive_normal(self):
        """정상 환경: ΔT > 0."""
        t_dry = 25.0
        t_dew = self._dew(t_dry, 60.0)
        self.assertGreater(t_dry - t_dew, 0)

    def test_delta_t_zero_at_rh100(self):
        """RH=100% → ΔT ≈ 0 (결로 발생 임계)."""
        t_dry = 25.0
        t_dew = self._dew(t_dry, 100.0)
        self.assertAlmostEqual(t_dry - t_dew, 0.0, delta=0.1)


class TestCondensationLevel(unittest.TestCase):
    """ΔT 기반 단계 판정 검증."""

    def _level(self, delta_t):
        return CondensationEngine._level(delta_t, _CFG)[0]

    def test_normal(self):
        self.assertEqual(self._level(6.0), AlertLevel.NONE)

    def test_level1_boundary(self):
        self.assertEqual(self._level(5.0), AlertLevel.LEVEL_1)   # ΔT = X
        self.assertEqual(self._level(4.0), AlertLevel.LEVEL_1)   # X > ΔT > Y

    def test_level2_boundary(self):
        self.assertEqual(self._level(3.0), AlertLevel.LEVEL_2)   # ΔT = Y
        self.assertEqual(self._level(1.5), AlertLevel.LEVEL_2)   # Y > ΔT > 0

    def test_level3_condensation(self):
        self.assertEqual(self._level(0.0),  AlertLevel.LEVEL_3)  # 결로 발생
        self.assertEqual(self._level(-1.0), AlertLevel.LEVEL_3)  # 이미 결로

    def test_cap_at_level3(self):
        """결로 영역 최대 단계는 LEVEL_3 (심각 없음)."""
        from domain.enums import DetectionDomain
        self.assertEqual(DetectionDomain.CONDENSATION.max_level, AlertLevel.LEVEL_3)

    def test_detail_message(self):
        """단계 판정 시 상세 메시지 포함 여부."""
        _, detail = CondensationEngine._level(-0.5, _CFG)
        self.assertIn("결로 발생", detail)

        _, detail = CondensationEngine._level(4.0, _CFG)
        self.assertIn("결로 관심", detail)

        _, detail = CondensationEngine._level(2.0, _CFG)
        self.assertIn("결로 주의", detail)


class TestSeasonDecision(unittest.TestCase):
    """계절 판정 테스트."""

    def _season(self, t_ext):
        from domain.enums import Season
        return CondensationEngine._season(t_ext, _CFG)

    def test_winter(self):
        from domain.enums import Season
        self.assertEqual(self._season(5.0),  Season.WINTER)
        self.assertEqual(self._season(12.0), Season.WINTER)

    def test_spring_fall(self):
        from domain.enums import Season
        self.assertEqual(self._season(15.0), Season.SPRING_FALL)
        self.assertEqual(self._season(23.0), Season.SPRING_FALL)

    def test_summer(self):
        from domain.enums import Season
        self.assertEqual(self._season(24.0), Season.SUMMER)
        self.assertEqual(self._season(35.0), Season.SUMMER)


if __name__ == "__main__":
    unittest.main(verbosity=2)

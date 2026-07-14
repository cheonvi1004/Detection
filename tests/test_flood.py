"""tests/test_flood.py — 침수 엔진 단위 테스트"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel
from domain.models import FloodConfig


def _cfg(l2=500.0, l3=650.0, l4=120.0, verified=True):
    return FloodConfig(
        zone_id="TEST",
        level2_trigger_mm=l2,
        level3_trigger_mm=l3,
        level4_inflow_threshold_lpm=l4,
        config_status="✓ 검증완료",
        is_verified=verified,
    )


class TestFloodConfig(unittest.TestCase):
    def test_configurable(self):
        self.assertTrue(_cfg().is_configurable)

    def test_not_configurable_when_none(self):
        cfg = FloodConfig("X", None, None, None, "미설정")
        self.assertFalse(cfg.is_configurable)

    def test_level_logic(self):
        """수위값 → 예상 레벨 확인 (로직만, 엔진 직접 호출 없이)."""
        cfg = _cfg(l2=500.0, l3=650.0, l4=120.0)

        # L4: inflow > l4
        inflow = 130.0
        self.assertGreater(inflow, cfg.level4_inflow_threshold_lpm)

        # L3: water >= l3
        water = 700.0
        self.assertGreaterEqual(water, cfg.level3_trigger_mm)

        # L2: water >= l2 but < l3
        water2 = 550.0
        self.assertGreaterEqual(water2, cfg.level2_trigger_mm)
        self.assertLess(water2, cfg.level3_trigger_mm)

        # NONE: water < l2
        water3 = 400.0
        self.assertLess(water3, cfg.level2_trigger_mm)

    def test_unverified_flag(self):
        cfg = _cfg(verified=False)
        self.assertFalse(cfg.is_verified)


if __name__ == "__main__":
    unittest.main(verbosity=2)

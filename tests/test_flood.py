"""tests/test_flood.py — 침수 엔진 단위 테스트 (DDL v2)"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.models import FloodConfig


def _cfg(inlet=500.0, offset=150.0, pump_count=2,
         pump_cap=50.0, margin=10.0, verified=True):
    total = pump_count * pump_cap if pump_count and pump_cap else None
    return FloodConfig(
        resource_id="TEST",
        inlet_pipe_height_mm=inlet,
        level3_offset_mm=offset,
        pump_count=pump_count,
        pump_capacity_lpm=pump_cap,
        pump_total_capacity_lpm=total,
        drain_disabled_margin_pct=margin,
        is_verified=verified,
    )


class TestFloodConfig(unittest.TestCase):
    def test_configurable(self):
        self.assertTrue(_cfg().is_configurable)

    def test_not_configurable_when_none(self):
        cfg = FloodConfig("X", inlet_pipe_height_mm=None)
        self.assertFalse(cfg.is_configurable)

    def test_level2_trigger(self):
        cfg = _cfg(inlet=500.0)
        self.assertEqual(cfg.level2_trigger_mm, 500.0)

    def test_level3_trigger(self):
        cfg = _cfg(inlet=500.0, offset=150.0)
        self.assertEqual(cfg.level3_trigger_mm, 650.0)

    def test_level4_threshold_with_margin(self):
        """pump 2대 × 50 L/min × (1 + 10%) = 110 L/min"""
        cfg = _cfg(pump_count=2, pump_cap=50.0, margin=10.0)
        self.assertAlmostEqual(cfg.level4_inflow_threshold_lpm, 110.0, delta=0.01)

    def test_level4_zero_margin(self):
        """margin=0% → 기준 = pump_total (마진 없음)"""
        cfg = _cfg(pump_count=2, pump_cap=50.0, margin=0.0)
        self.assertAlmostEqual(cfg.level4_inflow_threshold_lpm, 100.0, delta=0.01)

    def test_level4_none_when_no_pump(self):
        cfg = FloodConfig("X", inlet_pipe_height_mm=500.0,
                          pump_total_capacity_lpm=None)
        self.assertIsNone(cfg.level4_inflow_threshold_lpm)

    def test_unverified_flag(self):
        cfg = _cfg(verified=False)
        self.assertFalse(cfg.is_verified)

    def test_level_boundary_logic(self):
        """수위값 → 예상 레벨 경계 확인."""
        cfg = _cfg(inlet=500.0, offset=150.0, pump_count=2, pump_cap=50.0, margin=10.0)

        # 수위 600mm: L2(≥500) but L3 미충족(<650)
        water = 600.0
        self.assertGreaterEqual(water, cfg.level2_trigger_mm)
        self.assertLess(water, cfg.level3_trigger_mm)

        # 수위 700mm: L3(≥650)
        self.assertGreaterEqual(700.0, cfg.level3_trigger_mm)

        # 유입량 120 L/min: L4(>110)
        self.assertGreater(120.0, cfg.level4_inflow_threshold_lpm)


if __name__ == "__main__":
    unittest.main(verbosity=2)

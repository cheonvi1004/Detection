"""tests/test_models.py — 도메인 모델 단위 테스트 (DDL v2)"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel, DetectionDomain
from domain.models import ZoneStatus, DomainResult, FloodConfig, CondensationConfig


class TestZoneStatus(unittest.TestCase):
    def _status(self, current, prev=AlertLevel.NONE):
        results = {
            DetectionDomain.FIRE_GAS: DomainResult(
                resource_id="TEST", domain=DetectionDomain.FIRE_GAS, level=current
            )
        }
        return ZoneStatus(
            resource_id="TEST", zone_level=current,
            domain_results=results, previous_level=prev
        )

    def test_escalated(self):
        s = self._status(AlertLevel.LEVEL_2, AlertLevel.LEVEL_1)
        self.assertTrue(s.escalated)
        self.assertFalse(s.recovered)

    def test_recovered(self):
        s = self._status(AlertLevel.LEVEL_1, AlertLevel.LEVEL_3)
        self.assertFalse(s.escalated)
        self.assertTrue(s.recovered)

    def test_same_level(self):
        s = self._status(AlertLevel.LEVEL_2, AlertLevel.LEVEL_2)
        self.assertFalse(s.escalated)
        self.assertFalse(s.recovered)


class TestFloodConfigProperties(unittest.TestCase):
    def _cfg(self, inlet=500.0, offset=150.0, total=100.0, margin=10.0):
        return FloodConfig(
            resource_id="T",
            inlet_pipe_height_mm=inlet,
            level3_offset_mm=offset,
            pump_total_capacity_lpm=total,
            drain_disabled_margin_pct=margin,
        )

    def test_triggers(self):
        cfg = self._cfg(inlet=500.0, offset=150.0)
        self.assertEqual(cfg.level2_trigger_mm, 500.0)
        self.assertEqual(cfg.level3_trigger_mm, 650.0)

    def test_l4_threshold(self):
        cfg = self._cfg(total=100.0, margin=10.0)
        self.assertAlmostEqual(cfg.level4_inflow_threshold_lpm, 110.0)

    def test_none_inlet_not_configurable(self):
        cfg = FloodConfig("T", inlet_pipe_height_mm=None)
        self.assertFalse(cfg.is_configurable)
        self.assertIsNone(cfg.level2_trigger_mm)
        self.assertIsNone(cfg.level3_trigger_mm)


class TestCondensationConfigProperty(unittest.TestCase):
    def _cfg(self, y):
        return CondensationConfig(
            resource_id="T",
            coeff_a=610.78, coeff_b=17.2694, coeff_c=237.29,
            level2_delta_t=y,
        )

    def test_level1_auto_calc(self):
        self.assertAlmostEqual(self._cfg(3.0).level1_delta_t, 5.0,  delta=0.01)
        self.assertAlmostEqual(self._cfg(6.0).level1_delta_t, 10.0, delta=0.01)

    def test_level1_gt_level2(self):
        for y in [1.0, 2.0, 3.0, 5.0, 10.0]:
            cfg = self._cfg(y)
            self.assertGreater(cfg.level1_delta_t, cfg.level2_delta_t,
                               msg=f"y={y}: level1 should > level2")


if __name__ == "__main__":
    unittest.main(verbosity=2)

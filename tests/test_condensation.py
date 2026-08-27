"""tests/test_condensation.py — 실제 레포 CondensationGroup 구조 기준"""
import sys, os, unittest
from unittest.mock import MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel, DetectionDomain
from domain.models import CondensationConfig, CondensationGroup
from engine.condensation import CondensationEngine


def _cfg(level2: int = 5) -> CondensationConfig:
    cfg = CondensationConfig(
        resource_id="R0000001",
        coeff_a=610.78, coeff_b=17.2694, coeff_c=237.29,
        level2_delta_t=level2,
    )
    grp = CondensationGroup(group_id="G01")
    grp.wall_temp_sensor_ids = ["58-362"]
    grp.ext_temp_sensor_ids  = ["11-123"]
    grp.ext_humid_sensor_ids = ["11-124"]
    cfg.groups["G01"] = grp
    cfg.sensor_info_map["58-362"] = {"sid":"S01","sname":"벽체온도1","el_type":"SE000009"}
    cfg.sensor_info_map["11-123"] = {"sid":"S10","sname":"외기온도", "el_type":"SE000019"}
    cfg.sensor_info_map["11-124"] = {"sid":"S11","sname":"외기습도", "el_type":"SE000020"}
    return cfg


def _engine(cfg, influx_data):
    influx = MagicMock()
    pg     = MagicMock()
    pg.get_condensation_config.return_value  = cfg
    influx.get_condensation_data_multi.return_value = influx_data
    return CondensationEngine(influx, pg)


class TestCondensationEngine(unittest.TestCase):
    def test_no_data_returns_none(self):
        result = _engine(_cfg(), {"wall_temps":{},"ext_temps":{},"humidities":{}}).evaluate("R0000001")
        self.assertEqual(result.level, AlertLevel.NONE)

    def test_level2_warning(self):
        """T=22, RH=80 → ΔT≈3.6 ≤ Y=5 → 주의(L2)"""
        result = _engine(_cfg(level2=5), {
            "wall_temps": {"58-362": 22.0},
            "ext_temps":  {"11-123": 25.0},
            "humidities": {"11-124": 80.0},
        }).evaluate("R0000001")
        self.assertEqual(result.level, AlertLevel.LEVEL_2)
        self.assertGreater(len(result.triggered_sensors), 0)

    def test_level3_alert(self):
        """RH=100% → ΔT≈0 → 경계(L3)"""
        result = _engine(_cfg(level2=5), {
            "wall_temps": {"58-362": 22.0},
            "ext_temps":  {"11-123": 25.0},
            "humidities": {"11-124": 100.0},
        }).evaluate("R0000001")
        self.assertEqual(result.level, AlertLevel.LEVEL_3)

    def test_max_level_capped_at_level3(self):
        self.assertEqual(DetectionDomain.CONDENSATION.max_level, AlertLevel.LEVEL_3)

    def test_level1_humidity_trigger(self):
        """외기습도 ≥ 60% → 관심(L1) 이상"""
        result = _engine(_cfg(level2=5), {
            "wall_temps": {"58-362": 25.0},
            "ext_temps":  {"11-123": 20.0},
            "humidities": {"11-124": 65.0},
        }).evaluate("R0000001")
        self.assertGreaterEqual(result.level, AlertLevel.LEVEL_1)


class TestDewPoint(unittest.TestCase):
    def _dew(self, t, rh):
        return CondensationEngine._dew_point(t, rh, _cfg())

    def test_rh100_equals_dry(self):
        self.assertAlmostEqual(self._dew(25.0, 100.0), 25.0, delta=0.05)

    def test_known_25c_60rh(self):
        self.assertAlmostEqual(self._dew(25.0, 60.0), 16.7, delta=0.5)

    def test_lower_rh_lower_dew(self):
        self.assertGreater(self._dew(25.0, 80.0), self._dew(25.0, 50.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)

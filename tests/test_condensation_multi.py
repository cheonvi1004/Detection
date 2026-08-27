"""tests/test_condensation_multi.py — 다중 센서 결로 엔진 (CondensationGroup 구조)"""
import sys, os, unittest
from unittest.mock import MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel, DetectionDomain
from domain.models import CondensationConfig, CondensationGroup
from engine.condensation import CondensationEngine


def _cfg_multi(wall_srids, level2=5):
    cfg = CondensationConfig(
        resource_id="TEST",
        coeff_a=610.78, coeff_b=17.2694, coeff_c=237.29,
        level2_delta_t=level2,
    )
    grp = CondensationGroup(group_id="G01")
    grp.wall_temp_sensor_ids = wall_srids
    grp.ext_temp_sensor_ids  = ["T1"]
    grp.ext_humid_sensor_ids = ["H1"]
    cfg.groups["G01"] = grp
    for i, srid in enumerate(wall_srids):
        cfg.sensor_info_map[srid] = {"sid":f"SW{i+1}","sname":f"벽체{i+1}","el_type":"SE000009"}
    cfg.sensor_info_map["T1"] = {"sid":"ST1","sname":"외기온도","el_type":"SE000019"}
    cfg.sensor_info_map["H1"] = {"sid":"SH1","sname":"외기습도","el_type":"SE000020"}
    return cfg


def _engine(cfg, data):
    influx = MagicMock()
    pg     = MagicMock()
    pg.get_condensation_config.return_value  = cfg
    influx.get_condensation_data_multi.return_value = data
    return CondensationEngine(influx, pg)


class TestDewPoint(unittest.TestCase):
    def _dew(self, t, rh):
        cfg = _cfg_multi(["W1"])
        return CondensationEngine._dew_point(t, rh, cfg)

    def test_rh100_equals_dry(self):
        self.assertAlmostEqual(self._dew(25.0, 100.0), 25.0, delta=0.05)

    def test_known_25c_60rh(self):
        self.assertAlmostEqual(self._dew(25.0, 60.0), 16.7, delta=0.5)

    def test_lower_rh_lower_dew(self):
        self.assertGreater(self._dew(25.0, 80.0), self._dew(25.0, 50.0))


class TestWorstCase(unittest.TestCase):
    def test_worst_is_min_delta_t(self):
        """W3(RH=100%, T=22) → ΔT≈0 → 경계(Worst-case)"""
        cfg = _cfg_multi(["W1","W2","W3"], level2=5)
        result = _engine(cfg, {
            "wall_temps": {"W1":25.0, "W2":24.0, "W3":22.0},
            "ext_temps":  {"T1":30.0},
            "humidities": {"H1":100.0},
        }).evaluate("TEST")
        self.assertEqual(result.level, AlertLevel.LEVEL_3)

    def test_all_normal_returns_none(self):
        cfg = _cfg_multi(["W1","W2"], level2=5)
        result = _engine(cfg, {
            "wall_temps": {"W1":25.0,"W2":24.0},
            "ext_temps":  {"T1":15.0},
            "humidities": {"H1":30.0},
        }).evaluate("TEST")
        self.assertEqual(result.level, AlertLevel.NONE)

    def test_one_sensor_caution(self):
        """센서 3개 중 1개만 주의 조건 → 구역 L2 이상"""
        cfg = _cfg_multi(["W1","W2","W3"], level2=5)
        result = _engine(cfg, {
            "wall_temps": {"W1":25.0,"W2":25.0,"W3":22.0},
            "ext_temps":  {"T1":20.0},
            "humidities": {"H1":80.0},
        }).evaluate("TEST")
        self.assertGreaterEqual(result.level, AlertLevel.LEVEL_1)

    def test_max_level_level3(self):
        self.assertEqual(DetectionDomain.CONDENSATION.max_level, AlertLevel.LEVEL_3)


class TestMissing(unittest.TestCase):
    def test_empty_data_returns_none(self):
        cfg = _cfg_multi(["W1"])
        result = _engine(cfg, {"wall_temps":{},"ext_temps":{},"humidities":{}}).evaluate("TEST")
        self.assertEqual(result.level, AlertLevel.NONE)

    def test_no_humidity_returns_none(self):
        cfg = _cfg_multi(["W1"])
        result = _engine(cfg, {
            "wall_temps": {"W1":22.0},
            "ext_temps":  {"T1":25.0},
            "humidities": {},
        }).evaluate("TEST")
        self.assertEqual(result.level, AlertLevel.NONE)


class TestMultiGroup(unittest.TestCase):
    def test_two_groups_worst_wins(self):
        """G01 정상, G02 경계 → 구역 최종 LEVEL_3"""
        cfg = CondensationConfig(
            resource_id="TEST",
            coeff_a=610.78, coeff_b=17.2694, coeff_c=237.29,
            level2_delta_t=5,
        )
        g1 = CondensationGroup("G01")
        g1.wall_temp_sensor_ids=["W1"]; g1.ext_temp_sensor_ids=["T1"]; g1.ext_humid_sensor_ids=["H1"]
        g2 = CondensationGroup("G02")
        g2.wall_temp_sensor_ids=["W2"]; g2.ext_temp_sensor_ids=["T2"]; g2.ext_humid_sensor_ids=["H2"]
        cfg.groups = {"G01":g1,"G02":g2}
        for k,v in {
            "W1":{"sid":"SW1","sname":"벽체1","el_type":"SE000009"},
            "W2":{"sid":"SW2","sname":"벽체2","el_type":"SE000009"},
            "T1":{"sid":"ST1","sname":"외기온도1","el_type":"SE000019"},
            "T2":{"sid":"ST2","sname":"외기온도2","el_type":"SE000019"},
            "H1":{"sid":"SH1","sname":"외기습도1","el_type":"SE000020"},
            "H2":{"sid":"SH2","sname":"외기습도2","el_type":"SE000020"},
        }.items():
            cfg.sensor_info_map[k]=v

        result = _engine(cfg, {
            "wall_temps": {"W1":25.0,"W2":22.0},
            "ext_temps":  {"T1":20.0,"T2":25.0},
            "humidities": {"H1":40.0,"H2":100.0},
        }).evaluate("TEST")
        self.assertEqual(result.level, AlertLevel.LEVEL_3)


if __name__ == "__main__":
    unittest.main(verbosity=2)

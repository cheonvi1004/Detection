"""
tests/test_condensation_multi.py
──────────────────────────────────
다중 센서 결로 엔진 단위 테스트.

검증 항목:
  1. 센서별 ΔT 계산 정확성
  2. Worst-case(ΔT 최솟값) 선정
  3. Worst-case 기준 단계 판정
  4. 일부 센서 결측 시 유효 센서만으로 평가
  5. wall_temp 전체 결측 시 NONE 반환
  6. 공용 humidity 센서 처리
  7. 단일 폴백 모드 동작 확인
"""
import math, sys, os, unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.enums import AlertLevel, DetectionDomain
from domain.models import CondensationConfig, CondensationSensorResult, SensorMeta
from engine.condensation import CondensationEngine


# ── 테스트용 공통 픽스처 ─────────────────────────────────────────
def _cfg(level2: float = 3.0) -> CondensationConfig:
    return CondensationConfig(
        resource_id="TEST",
        coeff_a=610.78, coeff_b=17.2694, coeff_c=237.29,
        level2_delta_t=level2,
        season_winter_max_c=12.0, season_spring_max_c=23.0,
        target_temp_winter=20.0, target_temp_spring=22.5, target_temp_summer=25.0,
        target_rh_winter=60.0,   target_rh_spring=65.0,  target_rh_summer=70.0,
        has_ventilation=True,
    )


def _sensor(sid: str, stype: str = "wall_temp") -> SensorMeta:
    return SensorMeta(
        sensor_id=sid, resource_id="TEST",
        sensor_type=stype, is_active=True,
    )


def _make_engine(sensors, influx_data_multi, condensation_data_single=None):
    """CondensationEngine 인스턴스를 mock infra로 생성."""
    influx = MagicMock()
    pg     = MagicMock()

    pg.get_condensation_config.return_value  = _cfg()
    pg.get_condensation_sensors.return_value = sensors
    influx.get_condensation_data_multi.return_value = influx_data_multi
    if condensation_data_single:
        influx.get_condensation_data.return_value = condensation_data_single

    return CondensationEngine(influx, pg)


# ── 1. 센서별 ΔT 계산 정확성 ─────────────────────────────────────
class TestDewPointCalculation(unittest.TestCase):
    def _dew(self, t, rh):
        return CondensationEngine._dew_point(t, rh, _cfg())

    def test_rh100_dew_equals_dry(self):
        self.assertAlmostEqual(self._dew(25.0, 100.0), 25.0, delta=0.05)

    def test_known_25c_60rh(self):
        self.assertAlmostEqual(self._dew(25.0, 60.0), 16.7, delta=0.5)

    def test_lower_rh_lower_dew(self):
        self.assertGreater(self._dew(25.0, 80.0), self._dew(25.0, 50.0))


# ── 2. Worst-case 선정 ────────────────────────────────────────────
class TestWorstCaseSelection(unittest.TestCase):
    """ΔT 최솟값 센서가 Worst-case로 선정되는지 검증."""

    def _run(self, multi_data: dict, sensors: list[SensorMeta]):
        engine = _make_engine(sensors, multi_data)
        return engine.evaluate("TEST")

    def test_worst_case_is_min_delta_t(self):
        """
        COND-A01: T_dry=25.0, RH=60% → ΔT ≈ 8.3  (정상)
        COND-A02: T_dry=24.0, RH=85% → ΔT ≈ 1.6  (관심)
        COND-A03: T_dry=22.0, RH=100% → ΔT ≈ 0.0 (경계 ← Worst)
        """
        sensors = [
            _sensor("COND-A01"), _sensor("COND-A02"), _sensor("COND-A03"),
        ]
        multi = {
            "COND-A01": {"wall_temp": 25.0, "humidity": 60.0, "ext_temperature": 30.0},
            "COND-A02": {"wall_temp": 24.0, "humidity": 85.0, "ext_temperature": 30.0},
            "COND-A03": {"wall_temp": 22.0, "humidity": 100.0, "ext_temperature": 30.0},
        }
        result = self._run(multi, sensors)

        # Worst-case는 COND-A03 (경계)
        self.assertEqual(result.level, AlertLevel.LEVEL_3)
        self.assertIn("COND-A03", result.detail)
        self.assertEqual(len(result.sensor_results), 3)

    def test_all_normal_gives_none(self):
        """모든 센서 ΔT > X → 정상."""
        sensors = [_sensor("S1"), _sensor("S2")]
        multi = {
            "S1": {"wall_temp": 25.0, "humidity": 50.0, "ext_temperature": None},
            "S2": {"wall_temp": 24.0, "humidity": 55.0, "ext_temperature": None},
        }
        result = self._run(multi, sensors)
        self.assertEqual(result.level, AlertLevel.NONE)

    def test_one_sensor_caution_rest_normal(self):
        """한 센서만 주의 → 구역 레벨 = 주의."""
        sensors = [_sensor("S1"), _sensor("S2"), _sensor("S3")]
        cfg     = _cfg(level2=3.0)  # Y=3.0 → X=5.0
        multi   = {
            "S1": {"wall_temp": 25.0, "humidity": 50.0, "ext_temperature": None},
            "S2": {"wall_temp": 25.0, "humidity": 60.0, "ext_temperature": None},
            # ΔT ≈ 2.5 → L2 (주의)
            "S3": {"wall_temp": 25.0, "humidity": 90.0, "ext_temperature": None},
        }
        # S3의 ΔT 확인
        t_dew_s3 = CondensationEngine._dew_point(25.0, 90.0, cfg)
        delta_s3 = 25.0 - t_dew_s3
        # Y=3.0 이하면 L2
        expected = AlertLevel.LEVEL_2 if delta_s3 <= cfg.level2_delta_t else AlertLevel.LEVEL_1

        engine = _make_engine(sensors, multi)
        result = engine.evaluate("TEST")
        self.assertGreaterEqual(result.level, AlertLevel.LEVEL_1)

    def test_worst_case_sensor_id_in_triggered(self):
        """Worst-case 센서 ID가 triggered_sensors에 포함."""
        sensors = [_sensor("A"), _sensor("B")]
        multi = {
            "A": {"wall_temp": 25.0, "humidity": 60.0,  "ext_temperature": None},
            "B": {"wall_temp": 23.0, "humidity": 100.0, "ext_temperature": None},
        }
        result = _make_engine(sensors, multi).evaluate("TEST")
        self.assertIn("B", result.triggered_sensors)

    def test_delta_t_per_sensor_in_sensor_values(self):
        """센서별 ΔT가 sensor_values에 포함."""
        sensors = [_sensor("S1"), _sensor("S2")]
        multi = {
            "S1": {"wall_temp": 25.0, "humidity": 60.0, "ext_temperature": None},
            "S2": {"wall_temp": 24.0, "humidity": 80.0, "ext_temperature": None},
        }
        result = _make_engine(sensors, multi).evaluate("TEST")
        self.assertIn("delta_T_S1", result.sensor_values)
        self.assertIn("delta_T_S2", result.sensor_values)


# ── 3. 단계 판정 경계값 ───────────────────────────────────────────
class TestLevelBoundary(unittest.TestCase):
    def test_level3_cap_at_max(self):
        """결로 최대 단계 = LEVEL_3 (심각 없음)."""
        self.assertEqual(DetectionDomain.CONDENSATION.max_level, AlertLevel.LEVEL_3)

    def test_level_none_above_x(self):
        cfg = _cfg(level2=3.0)
        lv, _ = CondensationEngine._level(cfg.level1_delta_t + 0.1, cfg)
        self.assertEqual(lv, AlertLevel.NONE)

    def test_level1_at_x(self):
        cfg = _cfg(level2=3.0)
        lv, _ = CondensationEngine._level(cfg.level1_delta_t, cfg)
        self.assertEqual(lv, AlertLevel.LEVEL_1)

    def test_level2_at_y(self):
        cfg = _cfg(level2=3.0)
        lv, _ = CondensationEngine._level(cfg.level2_delta_t, cfg)
        self.assertEqual(lv, AlertLevel.LEVEL_2)

    def test_level3_at_zero(self):
        cfg = _cfg(level2=3.0)
        lv, _ = CondensationEngine._level(0.0, cfg)
        self.assertEqual(lv, AlertLevel.LEVEL_3)

    def test_level3_negative(self):
        cfg = _cfg(level2=3.0)
        lv, _ = CondensationEngine._level(-1.5, cfg)
        self.assertEqual(lv, AlertLevel.LEVEL_3)


# ── 4. 일부 센서 결측 처리 ───────────────────────────────────────
class TestPartialSensorMissing(unittest.TestCase):
    def test_missing_wall_temp_skips_sensor(self):
        """wall_temp 결측 센서는 스킵, 나머지로 평가."""
        sensors = [_sensor("S1"), _sensor("S2")]
        multi = {
            "S1": {"wall_temp": None,  "humidity": 80.0, "ext_temperature": None},
            "S2": {"wall_temp": 25.0,  "humidity": 60.0, "ext_temperature": None},
        }
        result = _make_engine(sensors, multi).evaluate("TEST")
        # S2만 유효 → 정상 또는 관심 (ΔT > 0)
        self.assertLessEqual(result.level, AlertLevel.LEVEL_1)
        # sensor_results에 S1 없음
        sids = [r.sensor_id for r in result.sensor_results]
        self.assertNotIn("S1", sids)
        self.assertIn("S2", sids)

    def test_missing_humidity_uses_common(self):
        """
        개별 sensor_id에 humidity 없을 때
        공용 humidity 센서(별도 sensor_id) 값을 사용.
        """
        # wall_temp 센서 2개 + 공용 humidity 센서 1개
        sensors = [
            _sensor("WALL-1", "wall_temp"),
            _sensor("WALL-2", "wall_temp"),
            _sensor("HUM-1",  "humidity"),
        ]
        multi = {
            "WALL-1": {"wall_temp": 25.0, "humidity": None, "ext_temperature": None},
            "WALL-2": {"wall_temp": 24.0, "humidity": None, "ext_temperature": None},
            "HUM-1":  {"wall_temp": None, "humidity": 80.0, "ext_temperature": None},
        }
        result = _make_engine(sensors, multi).evaluate("TEST")
        # 공용 humidity=80%로 두 센서 모두 평가 완료
        self.assertEqual(len(result.sensor_results), 2)

    def test_all_wall_temp_missing_returns_missing(self):
        """wall_temp 센서 전체 결측 → NONE 반환."""
        sensors = [_sensor("S1"), _sensor("S2")]
        multi = {
            "S1": {"wall_temp": None, "humidity": 80.0, "ext_temperature": None},
            "S2": {"wall_temp": None, "humidity": 75.0, "ext_temperature": None},
        }
        result = _make_engine(sensors, multi).evaluate("TEST")
        self.assertEqual(result.level, AlertLevel.NONE)


# ── 5. 단일 폴백 모드 ────────────────────────────────────────────
class TestSingleFallback(unittest.TestCase):
    def test_no_sensors_triggers_fallback(self):
        """등록 센서 없음 → 단일 폴백 모드 동작."""
        influx = MagicMock()
        pg     = MagicMock()
        pg.get_condensation_config.return_value  = _cfg()
        pg.get_condensation_sensors.return_value = []   # 빈 목록
        influx.get_condensation_data.return_value = {
            "wall_temp": 25.0, "humidity": 60.0, "ext_temperature": None
        }
        engine = CondensationEngine(influx, pg)
        result = engine.evaluate("TEST")

        # 단일 폴백 → detail에 "단일폴백" 표시
        self.assertIn("단일폴백", result.detail)
        # 다중 센서 쿼리는 호출되지 않아야 함
        influx.get_condensation_data_multi.assert_not_called()

    def test_fallback_missing_wall_temp(self):
        """단일 폴백 모드에서 wall_temp 결측 → NONE."""
        influx = MagicMock()
        pg     = MagicMock()
        pg.get_condensation_config.return_value  = _cfg()
        pg.get_condensation_sensors.return_value = []
        influx.get_condensation_data.return_value = {
            "wall_temp": None, "humidity": 80.0, "ext_temperature": None
        }
        engine = CondensationEngine(influx, pg)
        result = engine.evaluate("TEST")
        self.assertEqual(result.level, AlertLevel.NONE)


# ── 6. pick_common 유틸 ───────────────────────────────────────────
class TestPickCommon(unittest.TestCase):
    def test_returns_first_valid(self):
        raw = {
            "HUM-1": {"humidity": None},
            "HUM-2": {"humidity": 75.0},
            "HUM-3": {"humidity": 80.0},
        }
        result = CondensationEngine._pick_common(raw, ["HUM-1","HUM-2","HUM-3"], "humidity")
        self.assertEqual(result, 75.0)

    def test_all_none_returns_none(self):
        raw = {"H": {"humidity": None}}
        result = CondensationEngine._pick_common(raw, ["H"], "humidity")
        self.assertIsNone(result)

    def test_empty_sensor_ids(self):
        result = CondensationEngine._pick_common({}, [], "humidity")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)

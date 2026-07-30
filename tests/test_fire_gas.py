"""tests/test_fire_gas.py"""
import unittest
from unittest.mock import MagicMock
from engine.fire_gas import FireGasEngine
from domain.models import FireGasConfig
from domain.enums import AlertLevel
from config.settings import settings

class TestFireGasEngine(unittest.TestCase):
    def setUp(self):
        self.influx_mock = MagicMock()
        self.pg_mock = MagicMock()
        self.engine = FireGasEngine(self.influx_mock, self.pg_mock)

    def test_single_gas_alert(self):
        # 1. DB 설정 목업
        cfg = FireGasConfig(resource_id="R0000001", sensor_ids=["S1/1/SE000013"]) # CO 가스
        self.pg_mock.get_fire_gas_config.return_value = cfg

        # 2. InfluxDB 데이터 목업 (CO 임계치 초과 - 2600ppm -> LEVEL_3)
        self.influx_mock.get_fire_gas_data.return_value = {
            f"S1/1/{settings.FIRE_GAS_CO_TYPE}": {"current": 2600.0, "rise_per_min": 0.0}
        }

        result = self.engine.evaluate("R0000001")
        
        self.assertEqual(result.level, AlertLevel.LEVEL_3)
        self.assertEqual(result.triggered_sensors[0], "S1") # ID 정상 파싱 확인

    def test_escalation_to_level_4(self):
        cfg = FireGasConfig(resource_id="R0000001", sensor_ids=[
            "S1/1/SE000031", # 온도
            "S2/1/SE000013"  # CO
        ])
        self.pg_mock.get_fire_gas_config.return_value = cfg

        # 2가지 센서 모두 LEVEL_3 (경계) -> 심각(LEVEL_4)으로 격상되어야 함
        self.influx_mock.get_fire_gas_data.return_value = {
            f"S1/1/{settings.FIRE_GAS_TEMP_TYPE}": {"current": 76.0, "rise_per_min": 0.0}, # L3
            f"S2/1/{settings.FIRE_GAS_CO_TYPE}": {"current": 2600.0, "rise_per_min": 0.0}  # L3
        }

        result = self.engine.evaluate("R0000001")
        self.assertEqual(result.level, AlertLevel.LEVEL_4)
        self.assertIn("심각단계", result.detail)
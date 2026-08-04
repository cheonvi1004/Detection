"""tests/test_flood.py"""
import unittest
from unittest import result
from unittest.mock import MagicMock
from engine.flood import FloodEngine
from domain.models import FloodConfig
from domain.enums import AlertLevel
from utils.logger import get_logger

log = get_logger(__name__)

class TestFloodEngine(unittest.TestCase):
    def setUp(self):
        self.influx_mock = MagicMock()
        self.pg_mock = MagicMock()
        self.engine = FloodEngine(self.influx_mock, self.pg_mock)

    def test_skip_broken_sensor(self):
        cfg = FloodConfig(resource_id="R0000001", sensor_rl_ids=["RL_01"],sensor_info_map={
                        "RL_01": { "sid": "S01", "sname": "배수설비 W-01", "el_type": "SE000017"}
                    })
        self.pg_mock.get_flood_config.return_value = cfg
        
        # 센서 상태가 '고장'일 경우 수위가 높아도 정상 처리(스킵) 되어야 함
        self.pg_mock.get_flood_current_status.return_value = {
            "sensor_state": "고장",
            "waterLevelMm": 9999.0
        }

        result = self.engine.evaluate("R0000001")
        log.info(f"결과: {result}")
        self.assertEqual(result.level, AlertLevel.NONE)

    def test_drain_failure_escalation(self):
        cfg = FloodConfig(resource_id="R0000001", sensor_rl_ids=["RL_01"], inlet_pipe_height_mm=100,sensor_info_map={
                        "RL_01": { "sid": "S01", "sname": "배수설비 W-01", "el_type": "SE000017"}
                    })
        self.pg_mock.get_flood_config.return_value = cfg
        
        # [첫 번째 사이클] 펌프 ON, 수위 120mm (주의 단계)
        self.pg_mock.get_flood_current_status.return_value = {
            "sensor_state": "정상",
            "pump1_state": "ON",
            "waterLevelMm": 120.0,
            "HH_Lv_mm": 500.0
        }
        res1 = self.engine.evaluate("R0000001")
        log.info(f"res1 결과: {res1}")
        self.assertEqual(res1.level, AlertLevel.LEVEL_2)

        # [두 번째 사이클] 펌프가 켜져있는데 수위가 130mm로 상승 -> 배수불능(심각)
        self.pg_mock.get_flood_current_status.return_value = {
            "sensor_state": "정상",
            "pump1_state": "ON",
            "waterLevelMm": 130.0,
            "HH_Lv_mm": 500.0
        }
        res2 = self.engine.evaluate("R0000001")
        log.info(f"res2 결과: {res2}")
        self.assertEqual(res2.level, AlertLevel.LEVEL_4)
        self.assertIn("배수불능", res2.detail)
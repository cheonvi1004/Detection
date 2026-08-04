"""tests/test_structure.py"""
import unittest
from unittest.mock import MagicMock
from engine.structure import StructureEngine
from domain.models import StructureConfig
from domain.enums import AlertLevel
from config.settings import settings
from utils.logger import get_logger

log = get_logger(__name__)

class TestStructureEngine(unittest.TestCase):
    def setUp(self):
        self.influx_mock = MagicMock()
        self.pg_mock = MagicMock()
        self.engine = StructureEngine(self.influx_mock, self.pg_mock)

    def test_escalation_crack_and_vib(self):
        cfg = StructureConfig(resource_id="R0000001", sensor_ids=[
            "70-449",
            "71-435"
        ], sensor_info_map={
                        "70-449": { "sid": "S01", "sname": "균열", "el_type": "SE000001"},
                        "71-435": { "sid": "S02", "sname": "진동", "el_type": "SE000005"},
                       
                    })
        self.pg_mock.get_structure_config.return_value = cfg

        # 두 센서 모두 LEVEL_3 임계값 이상 도달 (균열 0.6 >= 0.5, 진동 1.2 >= 1.0)
        self.influx_mock.get_structure_data.return_value = {
            "70-449": {"current": 0.0},
            "71-435": {"current": 0.2}
        }

        result = self.engine.evaluate("R0000001")

        log.info(f"결과: {result}")
        # 두 가지 센서 경계(L3) 도달 시 심각(L4)으로 격상
        self.assertEqual(result.level, AlertLevel.LEVEL_4)
        self.assertEqual(len(result.triggered_sensors), 2)
        # ID가 하이픈으로 올바르게 잘려 병합되었는지 확인 ("70-449")
        self.assertIn("70-449", result.triggered_sensors)
        self.assertIn("71-435", result.triggered_sensors)
"""tests/test_structure.py"""
import unittest
from unittest.mock import MagicMock
from engine.structure import StructureEngine
from domain.models import StructureConfig
from domain.enums import AlertLevel
from config.settings import settings

class TestStructureEngine(unittest.TestCase):
    def setUp(self):
        self.influx_mock = MagicMock()
        self.pg_mock = MagicMock()
        self.engine = StructureEngine(self.influx_mock, self.pg_mock)

    def test_escalation_crack_and_vib(self):
        cfg = StructureConfig(resource_id="R0000001", sensor_ids=[
            f"70-449-{settings.STRUCTURE_CRACK_TYPE}",
            f"71-435-{settings.STRUCTURE_VIB_TYPE}"
        ])
        self.pg_mock.get_structure_config.return_value = cfg

        # 두 센서 모두 LEVEL_3 임계값 이상 도달 (균열 0.6 >= 0.5, 진동 1.2 >= 1.0)
        self.influx_mock.get_structure_data.return_value = {
            f"70-449-{settings.STRUCTURE_CRACK_TYPE}": {"current": 0.6},
            f"71-435-{settings.STRUCTURE_VIB_TYPE}": {"current": 1.2}
        }

        result = self.engine.evaluate("R0000001")
        
        # 두 가지 센서 경계(L3) 도달 시 심각(L4)으로 격상
        self.assertEqual(result.level, AlertLevel.LEVEL_4)
        self.assertEqual(len(result.triggered_sensors), 2)
        # ID가 하이픈으로 올바르게 잘려 병합되었는지 확인 ("70-449")
        self.assertIn("70-449", result.triggered_sensors)
        self.assertIn("71-435", result.triggered_sensors)
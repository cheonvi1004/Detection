"""tests/test_models.py"""
import unittest
from domain.models import DomainResult, FireGasConfig, FloodConfig, StructureConfig
from domain.enums import AlertLevel, DetectionDomain

class TestModels(unittest.TestCase):
    def test_domain_result_creation(self):
        result = DomainResult(
            resource_id="ZONE_01",
            domain=DetectionDomain.CONDENSATION,
            level=AlertLevel.LEVEL_1,
            triggered_sensors=["S001"],
            sensor_values={"S001": 25.5},
            detail="테스트"
        )
        self.assertEqual(result.resource_id, "ZONE_01")
        self.assertEqual(result.level, AlertLevel.LEVEL_1)
        self.assertIn("S001", result.triggered_sensors)

    def test_fire_gas_config_defaults(self):
        cfg = FireGasConfig(resource_id="ZONE_01")
        self.assertEqual(cfg.zone_id, "ZONE_01")
        self.assertEqual(cfg.temp_l3_threshold, 75.0)  # 기본값 확인
        self.assertEqual(len(cfg.sensor_ids), 0)

    def test_flood_config_defaults(self):
        cfg = FloodConfig(resource_id="ZONE_02")
        self.assertEqual(cfg.inlet_pipe_height_mm, 0.0)
        self.assertEqual(cfg.level3_offset_mm, 150.0)

    def test_structure_config_defaults(self):
        cfg = StructureConfig(resource_id="ZONE_03")
        self.assertEqual(cfg.crack_l3_threshold, 0.5)
        self.assertEqual(cfg.vib_l2_threshold, 0.5)
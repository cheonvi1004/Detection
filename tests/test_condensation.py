"""tests/test_condensation.py"""
import unittest
from unittest import result
from unittest.mock import MagicMock
from engine.condensation import CondensationEngine
from domain.enums import AlertLevel
from domain.models import CondensationConfig,CondensationGroup

from utils.logger import get_logger

log = get_logger(__name__)

class TestCondensationEngine(unittest.TestCase):
    def setUp(self):
        self.influx_mock = MagicMock()
        self.pg_mock = MagicMock()
        self.engine = CondensationEngine(self.influx_mock, self.pg_mock)

    def test_evaluate_condensation_warning(self):
        # 1. DB 센서 매핑 목업

        group_1=CondensationGroup(
            group_id="G01", 
            wall_temp_sensor_ids=["58-362"], 
            ext_temp_sensor_ids=["54-354"], 
            ext_humid_sensor_ids=["54-358"]
        )
        
        cfg = CondensationConfig(
            resource_id="R0000001",
            coeff_a=6.1078,
            coeff_b=17.2694,
            coeff_c=237.29,
            level2_delta_t=5.00,
            groups={"G01": group_1},
            sensor_info_map={
                "58-362": { "sid": "S01", "sname": "벽체온도1", "el_type": "SE01"},
                "54-354": { "sid": "S02", "sname": "외부온도2", "el_type": "SE02"},
                "54-358": { "sid": "S03", "sname": "외부습도1", "el_type": "SE03"}
            },
            ext_humid_l1_threshold=70.0, 
            ext_humid_l2_threshold=80.0, 
            ext_temp_l1_threshold=30.0
        )

        self.pg_mock.get_condensation_config.return_value = cfg
        
        # 2. InfluxDB 데이터 목업 (결로 발생 조건: 이슬점과 벽면 온도 차이가 2도 미만)
        # 온도 25도, 습도 80%일 때 이슬점은 약 21.3도
        # 벽면 온도가 22도라면 차이가 0.7도로 주의(LEVEL_2) 발령 대상
        self.influx_mock.get_condensation_data_multi.return_value = {
             "wall_temps": {"58-362": 22.0},
             "ext_temps": {"54-354": 25.0},
             "humidities": {"54-358": 80.0}
        }

        result = self.engine.evaluate("R0000001")

        log.info(f"결과: {result}")

        self.assertEqual(result.level, AlertLevel.LEVEL_2)
        self.assertIn("58-362", result.triggered_sensors)
        self.assertIn("group_id", result.sensor_values)

    def test_evaluate_normal(self):

        group_2=CondensationGroup(
            group_id="G02", 
            wall_temp_sensor_ids=["58-362"], 
            ext_temp_sensor_ids=["54-354"], 
            ext_humid_sensor_ids=["54-358"]
        )

        cfg = CondensationConfig(
                    resource_id="R0000001",
                    coeff_a=6.1078,
                    coeff_b=17.2694,
                    coeff_c=237.29,
                    level2_delta_t=5.00,  # 온도차 5.0도 미만일 때 주의 발령
                    groups={"G02": group_2},
                    sensor_info_map={
                            "58-362": { "sid": "S01", "sname": "벽체온도1", "el_type": "SE01"},
                            "54-354": { "sid": "S02", "sname": "외부온도2", "el_type": "SE02"},
                            "54-358": { "sid": "S03", "sname": "외부습도1", "el_type": "SE03"}
                    },
                    ext_humid_l1_threshold=70.0, 
                    ext_humid_l2_threshold=80.0, 
                    ext_temp_l1_threshold=30.0
                )
        

        self.pg_mock.get_condensation_config.return_value = cfg
        
        # 온도 25도, 습도 30% -> 이슬점 매우 낮음. 벽면 온도가 22도면 정상
        self.influx_mock.get_condensation_data_multi.return_value = {
            "wall_temps": {"58-362": 22.0},
            "ext_temps": {"54-354": 25.0},
            "humidities": {"54-358": 30.0}
        }

        result = self.engine.evaluate("R0000001")
        log.info(f"결과2: {result}")
        self.assertEqual(result.level, AlertLevel.NONE)
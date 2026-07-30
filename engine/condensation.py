from __future__ import annotations
import math
from typing import Optional

from domain.enums import AlertLevel, DetectionDomain
from domain.models import CondensationConfig, DomainResult
from engine.base import BaseDetectionEngine
from utils.logger import get_logger

log = get_logger(__name__)

class CondensationEngine(BaseDetectionEngine):
    domain = DetectionDomain.CONDENSATION

    def evaluate(self, resource_id: str) -> DomainResult:
        cfg = self.pg.get_condensation_config(resource_id)

        log.debug(f"[{resource_id}] get_condensation_config: {cfg}")

        if cfg is None or not cfg.groups:
            return self._missing_sensor(resource_id, "condensation_config_or_groups")

        # 1. 전체 하위 구역의 센서 ID를 모아 InfluxDB 1회 조회
        all_wall_ids, all_ext_temp_ids, all_ext_humid_ids = [], [], []
        for grp in cfg.groups.values():
            all_wall_ids.extend(grp.wall_temp_sensor_ids)
            all_ext_temp_ids.extend(grp.ext_temp_sensor_ids)
            all_ext_humid_ids.extend(grp.ext_humid_sensor_ids)

        data = self.influx.get_condensation_data_multi(all_wall_ids, all_ext_temp_ids, all_ext_humid_ids)

        log.debug(f"[{resource_id}] get_condensation_data_multi: {data}")

        wall_temps = data.get("wall_temps", {})
        ext_temps  = data.get("ext_temps", {})
        humidities = data.get("humidities", {})

        max_level = AlertLevel.NONE
        final_detail = "정상"
        final_sv = {}
        final_triggered = []

        # 2. 하위 구역(zone_01, zone_02 ...)별 평가
        for gid, grp in cfg.groups.items():
            grp_wall  = {sid: wall_temps[sid] for sid in grp.wall_temp_sensor_ids if sid in wall_temps}
            grp_ext_t = {sid: ext_temps[sid] for sid in grp.ext_temp_sensor_ids if sid in ext_temps}
            grp_humid = {sid: humidities[sid] for sid in grp.ext_humid_sensor_ids if sid in humidities}

            if not grp_wall or not grp_ext_t or not grp_humid:
                continue

            # 3. 구역 내 모든 센서 조합(조건) 순회 검사
            for ext_t_id, ext_t in grp_ext_t.items():
                for humid_id, rh in grp_humid.items():
                    for wall_id, wall_t in grp_wall.items():
                        
                        t_dew = self._dew_point(wall_t, rh, cfg)
                        delta_t = wall_t - t_dew

                        # 조합별 흐름도 평가 적용
                        grp_level, grp_detail = self._evaluate_flowchart(ext_t, rh, delta_t, gid, cfg)
                        
                        # 가장 위험한 조합 발견 시 최종 결과에 덮어쓰기
                        if grp_level > max_level:
                            max_level = grp_level
                            final_detail = grp_detail
                            final_triggered = [wall_id, ext_t_id, humid_id] if grp_level > AlertLevel.NONE else []
                            final_sv = {
                                "group_id": gid,
                                "ext_temperature": round(ext_t, 2),
                                "ext_humidity": round(rh, 2),
                                "wall_temp": round(wall_t, 2), 
                                "dew_point": round(t_dew, 2), 
                                "delta_T": round(delta_t, 2)
                            }

        if not final_sv:
            return self._missing_sensor(resource_id, "all_groups_data_empty")

        return DomainResult(
            resource_id=resource_id, 
            domain=self.domain,
            level=self._cap_level(max_level),
            triggered_sensors=final_triggered,
            sensor_values=final_sv, 
            detail=final_detail
        )

    @staticmethod
    def _evaluate_flowchart(t_ext: float, rh: float, delta_t: float, gid: str, cfg: CondensationConfig) -> tuple[AlertLevel, str]:
        """흐름도(Flowchart) 기반 단계별 조치사항 평가"""
        
        # [경계 단계]
        if delta_t <= 0:
            msg = f"[{gid}] 경계: ΔT({delta_t:.1f}℃) ≤ 0℃ (조치: 환기구 개방, 제습 설비 및 배기팬 가동)"
            return AlertLevel.LEVEL_3, msg
        
        # [주의 단계]
        if delta_t <= cfg.level2_delta_t:
            msg = f"[{gid}] 주의: ΔT({delta_t:.1f}℃) ≤ {cfg.level2_delta_t:5}℃ (조치: 환기구 차단, 제습 설비 가동)"
            return AlertLevel.LEVEL_2, msg
        if rh >= cfg.ext_humid_l2_threshold:
            msg = f"[{gid}] 주의: 외부습도({rh:.1f}%) ≥ 설정치({cfg.ext_humid_l2_threshold:.1f}%) (조치: 환기구 차단, 제습 설비 가동)"
            return AlertLevel.LEVEL_2, msg

        # [관심 단계]
        if rh >= cfg.ext_humid_l1_threshold:
            msg = f"[{gid}] 관심: 외부습도({rh:.1f}%) ≥ 설정치({cfg.ext_humid_l1_threshold:.1f}%) (조치: 노점/건구온도 주시, 시설물 점검)"
            return AlertLevel.LEVEL_1, msg
        if t_ext >= cfg.ext_temp_l1_threshold:
            msg = f"[{gid}] 관심: 외기온도({t_ext:.1f}℃) ≥ 설정치({cfg.ext_temp_l1_threshold:.1f}℃) (조치: 노점/건구온도 주시, 시설물 점검)"
            return AlertLevel.LEVEL_1, msg

        return AlertLevel.NONE, f"[{gid}] 정상"

    @staticmethod
    def _dew_point(t_dry: float, rh: float, cfg: CondensationConfig) -> float:
        """노점 온도 산출 공식"""
        a, b, c = cfg.coeff_a, cfg.coeff_b, cfg.coeff_c
        es = a * math.exp(b * t_dry / (c + t_dry))
        e  = (rh / 100.0) * es
        if e <= 0: return t_dry
        ln_r  = math.log(e / a)
        return c * ln_r / (b - ln_r)
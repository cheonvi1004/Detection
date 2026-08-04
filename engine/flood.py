"""engine/flood.py"""
from __future__ import annotations
from typing import Optional

from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult
from engine.base import BaseDetectionEngine
from utils.logger import get_logger

log = get_logger(__name__)

class FloodEngine(BaseDetectionEngine):
    domain = DetectionDomain.FLOOD

    def __init__(self, influx, pg):
        super().__init__(influx, pg)
        # 배수불능상태(유입량 > 배수량) 판단을 위해 엔진 메모리에 이전 수위를 기록합니다.
        self._prev_water_level: dict[str, float] = {}

    def evaluate(self, resource_id: str) -> DomainResult:
        # 1. DB에서 침수 설정 파라미터 조회
        cfg = self.pg.get_flood_config(resource_id)

        log.debug(f"[{resource_id}] get_flood_config: {cfg}")

        if not cfg or not cfg.sensor_rl_ids:
            return self._missing_sensor(resource_id, "flood_config_empty")

        max_level = AlertLevel.NONE
        final_detail = "정상"
        triggered_sensors = []
        sensor_results= []
        sensor_values = {}

        # 2. 펌프 센서별 최신 상태 검사
        for rl_id in cfg.sensor_rl_ids:

            info = cfg.sensor_info_map.get(rl_id, {})
            sid = info.get("sid", "UnknownID")
            sname = info.get("sname", "알 수 없는 센서")
            el_type = info.get("el_type", "UnknownType")
            
            status_data = self.pg.get_flood_current_status(rl_id)
            if not status_data:
                continue

            # JSON 페이로드에서 값 추출
            water_level = float(status_data.get("waterLevelMm", 0.0))
            pump1_state = status_data.get("pump1_state", "OFF")
            pump2_state = status_data.get("pump2_state", "OFF")
            hh_level = float(status_data.get("HH_Lv_mm", 9999.0))
            sensor_state = status_data.get("sensor_state", "정상")

            # 제일 먼저 센서 상태부터 확인!
            if not sensor_state =="정상":
                continue

            # 💡 [핵심] 유입량 > 배수량 판단 (펌프 가동 중 수위 상승 여부)
            prev_level = self._prev_water_level.get(rl_id)
            is_rising = prev_level is not None and water_level > prev_level
            self._prev_water_level[rl_id] = water_level

            is_pump_running = (pump1_state == "ON" or pump2_state == "ON")
            
            level = AlertLevel.NONE
            detail = ""

            # --- [흐름도 기반 판단 로직] ---
            # (1) 심각 (Level 4): 배수불능상태 (펌프 가동 중 수위 상승) 또는 최고위험수위(HH) 도달
            if (is_pump_running and is_rising) or water_level >= hh_level:
                level = AlertLevel.LEVEL_4
                detail = f"[침수 이상] {sname}({sid}) 배수불능/위험수위 (수위: {water_level}mm, 펌프가동중 수위상승 또는 HH도달)"
                
            # (2) 경계 (Level 3): 유입관 + 15cm(150mm) 이상
            elif water_level >= (cfg.inlet_pipe_height_mm + cfg.level3_offset_mm):
                level = AlertLevel.LEVEL_3
                detail = f"[침수 이상] {sname}({sid}) 경계수위 도달 (수위: {water_level}mm, 유입관+15cm 이상)"
                
            # (3) 주의 (Level 2): 유입관 높이 이상
            elif water_level >= cfg.inlet_pipe_height_mm:
                level = AlertLevel.LEVEL_2
                detail = f"[침수 이상] {sname}({sid}) 주의수위 도달 (수위: {water_level}mm, 유입관 도달)"

            # --- 결과 병합 (가장 위험한 센서를 0번 인덱스로) ---
            if level > AlertLevel.NONE:
                # 스냅샷 저장을 위해 펌프 상태값도 함께 기록합니다.
                sensor_values[f"{sid}_level_mm"] = water_level
                sensor_values[f"{sid}_pump1"] = pump1_state
                sensor_values[f"{sid}_pump2"] = pump2_state

                final_detail = detail

                triggered_sensors.append(sid)

                # 3. 💡 개별 센서 이벤트 추출 (DomainResult의 sensor_results에 전달)
                sensor_results.append({
                    "sensor_id": sid,
                    "element_type": el_type,
                    "level": level,
                    "value": "",
                    "detail": final_detail
                })
                
                if level > max_level:
                    max_level = level
                 

        if not sensor_values:
            return self._missing_sensor(resource_id, "no_active_flood_data")

        # --- 조치사항 텍스트 매핑 ---
        #if max_level == AlertLevel.LEVEL_4:
        #     final_detail += " (조치: 해당 구역 즉시 대피, 모든 배수설비 가동, 재난 대응 소관부서 지원요청)"
        #elif max_level == AlertLevel.LEVEL_3:
        #    final_detail += " (조치: 구역 내 작업자 즉시 대피, 펌프제어반/배수설비 추가 가동)"
        #elif max_level == AlertLevel.LEVEL_2:
        #    final_detail += " (조치: 주의 알림 발송, 누수/외부요인 침수 파악 및 펌프제어반 작동)"

        return DomainResult(
            resource_id=resource_id,
            domain=self.domain,
            level=self._cap_level(max_level),
            triggered_sensors=triggered_sensors,
            sensor_values=sensor_values,
            detail=final_detail,
            sensor_results=sensor_results
        )
"""engine/structure.py"""
from __future__ import annotations
from typing import Optional

from config.settings import settings
from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult, StructureConfig
from engine.base import BaseDetectionEngine
from utils.logger import get_logger

log = get_logger(__name__)

class StructureEngine(BaseDetectionEngine):
    domain = DetectionDomain.STRUCTURE

    def evaluate(self, resource_id: str) -> DomainResult:
        # 1. DB에서 해당 구역의 구조물 설정(임계값 포함) 조회
        cfg = self.pg.get_structure_config(resource_id)
        if not cfg or not cfg.sensor_ids:
            return self._missing_sensor(resource_id, "structure_config_empty")

        log.debug(f"[{resource_id}] get_structure_config: {cfg}")

        # 2. InfluxDB 데이터 조회 (센서 ID 목록 전달)
        # ※ 구조물 센서 데이터를 가져오는 influx 메서드 호출 (구현 필요 시 추가)
        data = self.influx.get_structure_data(cfg.sensor_ids)

        log.debug(f"[{resource_id}] get_structure_data: {data}")

        max_level = AlertLevel.NONE
        final_detail = "정상"
        triggered_sensors = []
        sensor_results= []
        sensor_values = {}

        # 복합 조건(에스컬레이션) 검사를 위한 위험도 카운트
        level_counts = {
            AlertLevel.LEVEL_3: 0,
            AlertLevel.LEVEL_2: 0,
            AlertLevel.LEVEL_1: 0
        }

        # 3. 개별 센서 및 Element Type 순회하며 DB 임계값으로 평가
        for original_id, s_data in data.items():
            
            info = cfg.sensor_info_map.get(original_id, {})
            sid = info.get("sid", "UnknownID")
            sname = info.get("sname", "알 수 없는 센서")
            el_type = info.get("el_type", "UnknownType")

            current_val = s_data.get("current", 0.0)
            level = AlertLevel.NONE
            detail = ""

            # --- [균열 판단 로직] ---
            if el_type == settings.STRUCTURE_CRACK_TYPE:
                if current_val >= cfg.crack_l3_threshold:
                    level = AlertLevel.LEVEL_3
                    detail = f"[구조물 이상] {sname}({sid}) 균열폭 {current_val}mm (설정치 {cfg.crack_l3_threshold}mm 이상)"
                elif current_val >= cfg.crack_l2_threshold:
                    level = AlertLevel.LEVEL_2
                    detail = f"[구조물 이상] {sname}({sid}) 균열폭 {current_val}mm (설정치 {cfg.crack_l2_threshold}mm 이상)"
                elif current_val >= cfg.crack_l1_threshold:
                    level = AlertLevel.LEVEL_1
                    detail = f"[구조물 이상] {sname}({sid}) 균열폭 {current_val}mm (설정치 {cfg.crack_l1_threshold}mm 이상)"

            # --- [진동 판단 로직] ---
            elif el_type == settings.STRUCTURE_VIB_TYPE:
                if current_val >= cfg.vib_l3_threshold:
                    level = AlertLevel.LEVEL_3
                    detail = f"[구조물 이상] {sname}({sid}) 진동가속도 {current_val}cm/sec (설정치 {cfg.vib_l3_threshold}cm/sec 이상)"
                elif current_val >= cfg.vib_l2_threshold:
                    level = AlertLevel.LEVEL_2
                    detail = f"[구조물 이상] {sname}({sid}) 진동가속도 {current_val}cm/sec (설정치 {cfg.vib_l2_threshold}cm/sec 이상)"
                elif current_val >= cfg.vib_l1_threshold:
                    level = AlertLevel.LEVEL_1
                    detail = f"[구조물 이상] {sname}({sid}) 진동가속도 {current_val}cm/sec (설정치 {cfg.vib_l1_threshold}cm/sec 이상)"


            log.info(f"[{resource_id}|{sid}] level: {level}")
            # --- 💡 임계치를 초과한 센서 추출 및 정렬 처리 ---
            if level > AlertLevel.NONE:
                level_counts[level] += 1
                
              
                if level > max_level:
                    max_level = level

                final_detail = detail
                final_sensor_id = sid
                final_el_type = el_type
                
                triggered_sensors.append(sid)

                sensor_values[f"{sid}_current"] = current_val

                if max_level == AlertLevel.LEVEL_3:
                    final_detail += " (조치: 유효 범위 내 작업 중지 알림, 보수·보강)"
                elif max_level == AlertLevel.LEVEL_2:
                    final_detail += " (조치: 공동구 구조적 안전을 위한 대책 수립 또는 외부 협조 요청)"
                elif max_level == AlertLevel.LEVEL_1:
                    final_detail += " (조치: 균열, 진동 요인 확인 및 비상 근무 체계 편성)"

                # 3. 💡 개별 센서 이벤트 추출 (DomainResult의 sensor_results에 전달)
                sensor_results.append({
                    "sensor_id": sid,
                    "element_type": el_type,
                    "level": level,
                    "value": current_val,
                    "detail": final_detail
                })
                #else:
                #    if sid not in triggered_sensors:
                #       triggered_sensors.append(sid)

        final_detail=""
                 
        # 4. [흐름도 복합 조건] 2가지 센서 동시 감지 시 격상(Escalation)
        if level_counts[AlertLevel.LEVEL_3] >= 2:
            max_level = AlertLevel.LEVEL_4
            final_detail = "[구조물 이상] 2가지 센서 동시 '경계' -> [심각단계] 격상 (조치: 해당 구역 비상 발전/조명 가동, 보수·보강 및 피해복구)"

        # 단일 조건인 경우 흐름도에 명시된 후속 조치사항 메시지 결합
        #else:
        #    if max_level == AlertLevel.LEVEL_3:
        #        final_detail += " (조치: 유효 범위 내 작업 중지 알림, 보수·보강)"
        #    elif max_level == AlertLevel.LEVEL_2:
        #        final_detail += " (조치: 공동구 구조적 안전을 위한 대책 수립 또는 외부 협조 요청)"
        #   elif max_level == AlertLevel.LEVEL_1:
        #        final_detail += " (조치: 균열, 진동 요인 확인 및 비상 근무 체계 편성)"

        if not sensor_values:
            return self._missing_sensor(resource_id, "all_structure_data_normal_or_empty")

        return DomainResult(
            resource_id=resource_id,
            domain=self.domain,
            level=self._cap_level(max_level),
            triggered_sensors=triggered_sensors,
            sensor_values=sensor_values,
            detail=final_detail,
            sensor_results=sensor_results
        )
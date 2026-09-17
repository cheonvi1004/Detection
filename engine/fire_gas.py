"""engine/fire_gas.py"""
from __future__ import annotations
from typing import Optional

from config.settings import settings
from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult, FireGasConfig
from engine.base import BaseDetectionEngine
from utils.logger import get_logger

log = get_logger(__name__)

class FireGasEngine(BaseDetectionEngine):
    domain = DetectionDomain.FIRE_GAS

    def evaluate(self, resource_id: str) -> DomainResult:
        # 1. DB에서 해당 구역의 화재/가스 설정(임계값 포함) 조회
        cfg = self.pg.get_fire_gas_config(resource_id)

        log.debug(f"[{resource_id}] get_fire_gas_config: {cfg}")
        
        if not cfg or not cfg.sensor_ids:
            return self._missing_sensor(resource_id, "fire_gas_config_empty")

        # 2. InfluxDB 데이터 조회 (센서 ID 목록 전달)
        # ※ influx.get_fire_gas_data_multi() 등 구현된 메서드로 변경 필요

        log.debug(f"[{resource_id}] get_fire_gas_data: {cfg.sensor_ids}")
        data = self.influx.get_fire_gas_data(cfg.sensor_ids)

        log.debug(f"[{resource_id}] data: {data}")

        max_level = AlertLevel.NONE
        final_detail = "정상"
        triggered_sensors = []
        sensor_results  = []
        sensor_values = {}

        # 복합 조건 검사를 위한 위험도 카운트
        level_counts = {
            AlertLevel.LEVEL_3: 0,
            AlertLevel.LEVEL_2: 0,
            AlertLevel.LEVEL_1: 0
        }

        # 3. 개별 센서 및 Element Type 순회하며 DB 임계값으로 평가
        for s_rlid, s_data in data.items():
          
            info = cfg.sensor_info_map.get(s_rlid, {})
            sid = info.get("sid", "UnknownID")
            sensor_name = info.get("sname", "알 수 없는 센서")
            el_type = info.get("el_type", "UnknownType")
            
            current_val = s_data.get("current", 0.0)
            
            level = AlertLevel.NONE
            detail = ""

            # --- [온도 판단 로직] ---
            if el_type == settings.FIRE_GAS_TEMP_TYPE:
                rise_per_min = s_data.get("rise_per_min", 0.0) # 1분전 대비 상승률

                # 💡 [추가] 센서별 온도 평가 상세 로그
                log.debug(f"[{resource_id}] {sensor_name}({sid}) - 온도: {current_val}℃ (임계 L2:{cfg.temp_l2_threshold}, L3:{cfg.temp_l3_threshold}) / 분당상승: {rise_per_min}℃ (임계 L1:{cfg.temp_l1_rise_threshold})")

                if current_val >= cfg.temp_l3_threshold:
                    level = AlertLevel.LEVEL_3
                    detail = f"[화재이상] {sensor_name}({sid}) {current_val}℃ (설정치 {cfg.temp_l3_threshold}℃ 이상)"
                elif current_val >= cfg.temp_l2_threshold:
                    level = AlertLevel.LEVEL_2
                    detail = f"[화재이상] {sensor_name}({sid}) {current_val}℃ (설정치 {cfg.temp_l2_threshold}℃ 이상)"
                elif rise_per_min >= cfg.temp_l1_rise_threshold:
                    level = AlertLevel.LEVEL_1
                    detail = f"[화재이상] {sensor_name}({sid}) 분당 온도상승 {rise_per_min}℃ (설정치 {cfg.temp_l1_rise_threshold}℃ 이상)"

            # --- [가스 판단 로직] ---
            else:
                level, detail = self._evaluate_gas(el_type, current_val, cfg,sid,sensor_name)

            # --- 💡 핵심: 임계치를 초과한(감지된) 센서 처리 ---
            if level > AlertLevel.NONE:
                level_counts[level] += 1
                
                # 센서 측정값 스냅샷 저장
                sensor_values[f"{sid}_{el_type}"] = current_val

                final_detail = detail

                # 3. 💡 개별 센서 이벤트 추출 (DomainResult의 sensor_results에 전달)
                sensor_results.append({
                    "sensor_id": sid,
                    "element_type": el_type,
                    "level": level,
                    "value": "",
                    "detail": final_detail
                })

                triggered_sensors.append(sid)
                
                
                # 현재 센서가 지금까지 발견된 다른 센서들보다 위험도가 높다면?
                if level > max_level:
                    max_level = level
                

        # 4. [흐름도 복합 조건] 2가지 센서 이상 감지 시 격상(Escalation)
        # 경계(L3) 2개 이상 -> 심각(L4) 격상
        if level_counts[AlertLevel.LEVEL_3] >= 2:
            max_level = AlertLevel.LEVEL_4
            final_detail = "[화재이상] 2가지 이상 센서 동시 '경계' -> [심각단계] 격상 (조치: 화재수신기/방화문 작동, 환기구 오픈, 배기팬 가동 등)"
            
        # 주의(L2) 2개 이상 -> 경계(L3) 격상
        elif level_counts[AlertLevel.LEVEL_2] >= 2 and max_level < AlertLevel.LEVEL_3:
            max_level = AlertLevel.LEVEL_3
            final_detail = "[화재이상] 2가지 이상 센서 동시 '주의' -> [경계단계] 격상 (조치: 작업자 경계 알림, 구역 출입 통제 등)"

        # 단일 조건인 경우 후속 조치사항 메시지 결합
        #else:
        #    if max_level == AlertLevel.LEVEL_3:
        #        final_detail += " (조치: 작업자 경계 알림, 구역 출입 통제 등)"
        #    elif max_level == AlertLevel.LEVEL_2:
        #        final_detail += " (조치: 작업자 주의 알림, 배전반 과열 확인 등)"
        #    elif max_level == AlertLevel.LEVEL_1:
        #        final_detail += " (조치: 시설 점검, 발열 지점 확인 등)"

        if not sensor_values:
            return self._missing_sensor(resource_id, "all_fire_gas_data_normal_or_empty")

        return DomainResult(
            resource_id=resource_id,
            domain=self.domain,
            level=self._cap_level(max_level),
            triggered_sensors=triggered_sensors,
            sensor_values=sensor_values,
            detail=final_detail,
            sensor_results=sensor_results
        )

    def _evaluate_gas(self, el_type: str, val: float, cfg: FireGasConfig,sid: str, sensor_name: str) -> tuple[AlertLevel, str]:
        """엘리먼트 타입(가스 종류)과 DB 설정 임계값을 매칭하여 평가"""
        
        # 1. 산소 (O2) - 낮아질수록 위험 (이하 조건)
        if el_type == settings.FIRE_GAS_O2_TYPE:
            log.debug(f"[{cfg.resource_id}] {sensor_name}({sid}) - O2: {val}% (임계 L1:{cfg.o2_l1_threshold}, L2:{cfg.o2_l2_threshold}, L3:{cfg.o2_l3_threshold} 이하)")
            if val <= cfg.o2_l3_threshold: return AlertLevel.LEVEL_3, f"[화재이상] {sensor_name}({sid}) 산소 {val}% (설정치 {cfg.o2_l3_threshold}% 이하)"
            if val <= cfg.o2_l2_threshold: return AlertLevel.LEVEL_2, f"[화재이상] {sensor_name}({sid}) 산소 {val}% (설정치 {cfg.o2_l2_threshold}% 이하)"
            if val <= cfg.o2_l1_threshold: return AlertLevel.LEVEL_1, f"[화재이상] {sensor_name}({sid}) 산소 {val}% (설정치 {cfg.o2_l1_threshold}% 이하)"
            
        # 2. 일산화탄소 (CO)
        elif el_type == settings.FIRE_GAS_CO_TYPE:
            log.debug(f"[{cfg.resource_id}] {sensor_name}({sid}) - CO: {val}ppm (임계 L1:{cfg.co_l1_threshold}, L2:{cfg.co_l2_threshold}, L3:{cfg.co_l3_threshold} 이상)")
            if val >= cfg.co_l3_threshold: return AlertLevel.LEVEL_3, f"[화재이상] {sensor_name}({sid}) 일산화탄소 {val}ppm (설정치 {cfg.co_l3_threshold} 이상)"
            if val >= cfg.co_l2_threshold: return AlertLevel.LEVEL_2, f"[화재이상] {sensor_name}({sid}) 일산화탄소 {val}ppm (설정치 {cfg.co_l2_threshold} 이상)"
            if val >= cfg.co_l1_threshold: return AlertLevel.LEVEL_1, f"[화재이상] {sensor_name}({sid}) 일산화탄소 {val}ppm (설정치 {cfg.co_l1_threshold} 이상)"
            
        # 3. 이산화탄소 (CO2)
        elif el_type == settings.FIRE_GAS_CO2_TYPE:

            # 💡 센서에서 올라온 ppm 값을 % 단위로 변환 (10,000으로 나눔)
            val_percent = val / 10000.0

            log.debug(f"[{cfg.resource_id}] {sensor_name}({sid}) - CO2: {val_percent}% (임계 L1:{cfg.co2_l1_threshold}, L2:{cfg.co2_l2_threshold}, L3:{cfg.co2_l3_threshold} 이상)")
            if val_percent >= cfg.co2_l3_threshold: return AlertLevel.LEVEL_3, f"[화재이상] {sensor_name}({sid}) 이산화탄소 {val_percent}% (설정치 {cfg.co2_l3_threshold} 이상)"
            if val_percent >= cfg.co2_l2_threshold: return AlertLevel.LEVEL_2, f"[화재이상] {sensor_name}({sid}) 이산화탄소 {val_percent}% (설정치 {cfg.co2_l2_threshold} 이상)"
            if val_percent >= cfg.co2_l1_threshold: return AlertLevel.LEVEL_1, f"[화재이상] {sensor_name}({sid}) 이산화탄소 {val_percent}% (설정치 {cfg.co2_l1_threshold} 이상)"
            
        # 4. 황화수소 (H2S)
        elif el_type == settings.FIRE_GAS_H2S_TYPE:
            log.debug(f"[{cfg.resource_id}] {sensor_name}({sid}) - H2S: {val}ppm (임계 L1:{cfg.h2s_l1_threshold}, L2:{cfg.h2s_l2_threshold}, L3:{cfg.h2s_l3_threshold} 이상)")
            if val >= cfg.h2s_l3_threshold: return AlertLevel.LEVEL_3, f"[화재이상] {sensor_name}({sid}) 황화수소 {val}ppm (설정치 {cfg.h2s_l3_threshold} 이상)"
            if val >= cfg.h2s_l2_threshold: return AlertLevel.LEVEL_2, f"[화재이상] {sensor_name}({sid}) 황화수소 {val}ppm (설정치 {cfg.h2s_l2_threshold} 이상)"
            if val >= cfg.h2s_l1_threshold: return AlertLevel.LEVEL_1, f"[화재이상] {sensor_name}({sid}) 황화수소 {val}ppm (설정치 {cfg.h2s_l1_threshold} 이상)"
            
        return AlertLevel.NONE, ""
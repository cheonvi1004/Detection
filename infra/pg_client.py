"""
infra/pg_client.py
───────────────────
PostgreSQL sysmaster 스키마 조회 클라이언트.

v2.1 변경:
- get_condensation_sensors(): 결로 감지용 센서 목록 조회 추가
  (wall_temp + humidity 쌍을 sensor_id 기준으로 반환)
"""
from __future__ import annotations
from contextlib import contextmanager
from typing import Optional

import psycopg2, psycopg2.extras
from config.settings import settings
from domain.models import CondensationConfig, FloodConfig, ThresholdRow, SensorMeta, CondensationGroup, FireGasConfig, StructureConfig
from domain.enums import AlertLevel, ThresholdOp, AggregationFn
from utils.logger import get_logger

log = get_logger(__name__)


class PgRepo:
    def __init__(self) -> None:
        self._conn = None

    def connect(self) -> None:
        self._conn = psycopg2.connect(settings.pg.dsn)
        self._conn.autocommit = True
        log.info("PostgreSQL 연결 완료 (schema: %s)", settings.pg.schema)

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()

    @contextmanager
    def _cur(self):
        if self._conn is None or self._conn.closed:
            self.connect()
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
        finally:
            cur.close()

    # ── 활성 구역 목록 ────────────────────────────────────────────
    def get_active_resources(self) -> list[dict]:
        with self._cur() as c:
            c.execute("""
                SELECT DISTINCT resource_id
                FROM (
                    SELECT resource_id FROM anomaly_thresholds  WHERE is_active = TRUE
                    UNION
                    SELECT resource_id FROM anomaly_flood_parameters
                    UNION
                    SELECT resource_id FROM anomaly_condensation_thresholds
                     WHERE resource_id IS NOT NULL
                ) r
                ORDER BY resource_id
            """)
            return [dict(r) for r in c.fetchall()]

    # ── 결로 센서 목록 조회 ───────────────────────────────────────
    def get_condensation_sensors(self, resource_id: str) -> list[SensorMeta]:
        """
        anomaly_sensors 테이블에서 결로 관련 센서(wall_temp, humidity) 목록 조회.

        DDL 예시 (실제 운영 환경에 맞게 테이블명/컬럼명 수정):
            CREATE TABLE anomaly_sensors (
                sensor_id    VARCHAR(30) PRIMARY KEY,
                resource_id  VARCHAR(8)  NOT NULL,
                sensor_type  VARCHAR(60) NOT NULL,  -- 'wall_temp' | 'humidity' | 'ext_temperature'
                location_desc TEXT,
                is_active    BOOLEAN NOT NULL DEFAULT TRUE
            );

        센서가 없으면 빈 리스트 반환 → 엔진에서 단일 센서 폴백 처리.
        """
        try:
            with self._cur() as c:
                c.execute("""
                    SELECT sensor_id, resource_id, sensor_type,
                           location_desc, is_active
                    FROM anomaly_sensors
                    WHERE resource_id = %s
                      AND sensor_type IN ('wall_temp', 'humidity', 'ext_temperature')
                      AND is_active = TRUE
                    ORDER BY sensor_id
                """, (resource_id,))
                rows = c.fetchall()
        except psycopg2.errors.UndefinedTable:
            # anomaly_sensors 테이블 미존재 시 빈 리스트 반환
            log.debug("[%s] anomaly_sensors 테이블 없음 — 단일 센서 모드로 폴백", resource_id)
            return []

        return [
            SensorMeta(
                sensor_id=r["sensor_id"],
                resource_id=r["resource_id"],
                sensor_type=r["sensor_type"],
                location_desc=r.get("location_desc"),
                is_active=bool(r["is_active"]),
            )
            for r in rows
        ]

    # ── 일반 임계값 조회 (화재/가스, 구조) ───────────────────────
    def get_thresholds(self, resource_id: str, sensor_category: str) -> list[ThresholdRow]:
        with self._cur() as c:
            c.execute("""
                SELECT
                    resource_id, sensor_category, sensor_element_type,
                    alert_level, operator, threshold_value,
                    aggregation, eval_window_sec,
                    composite_group, composite_min_count,
                    composite_target_level, description, is_active
                FROM anomaly_thresholds
                WHERE sensor_category = %s
                  AND is_active = TRUE
                  AND (resource_id = %s
                       OR resource_id = 'DEFAULT'
                       OR resource_id IS NULL)
                ORDER BY
                    CASE WHEN resource_id = %s THEN 0 ELSE 1 END,
                    alert_level, sensor_element_type
            """, (sensor_category, resource_id, resource_id))
            rows = c.fetchall()

        result, seen = [], set()
        for r in rows:
            key = (r["sensor_element_type"], r["alert_level"])
            if key in seen:
                continue
            seen.add(key)
            ctl = r.get("composite_target_level")
            result.append(ThresholdRow(
                resource_id           = r["resource_id"] or "DEFAULT",
                sensor_category       = r["sensor_category"],
                sensor_element_type   = r["sensor_element_type"],
                alert_level           = AlertLevel[r["alert_level"]]
                                        if isinstance(r["alert_level"], str)
                                        else AlertLevel(int(r["alert_level"])),
                operator              = ThresholdOp(r["operator"]),
                threshold_value       = float(r["threshold_value"]),
                aggregation           = AggregationFn(r["aggregation"]),
                eval_window_sec       = int(r["eval_window_sec"]),
                composite_group       = r.get("composite_group"),
                composite_min_count   = int(r.get("composite_min_count") or 1),
                composite_target_level= AlertLevel[ctl] if ctl else None,
                description           = r.get("description"),
                is_active             = bool(r["is_active"]),
            ))
        return result

    # ── 결로 설정 조회 ────────────────────────────────────────────
    def get_condensation_config(self, resource_id: str) -> Optional[CondensationConfig]:
        with self._cur() as c:
            # 1. 결로 공식 계수 조회
            c.execute("""
                SELECT 
                    COALESCE(t.resource_id, %s) AS resource_id,
                    f.a AS coeff_a, f.b AS coeff_b, f.c AS coeff_c,level2_delta_t
                FROM anomaly_condensation_thresholds t
                JOIN anomaly_condensation_formula_coefficients f ON t.coeff_id = f.coeff_id
                WHERE t.resource_id = %s OR t.resource_id IS NULL
                ORDER BY t.resource_id NULLS LAST LIMIT 1
            """, (resource_id, resource_id))
            
            row = c.fetchone()
            if not row:
                return None

            config = CondensationConfig(
                resource_id=row["resource_id"],
                coeff_a=float(row["coeff_a"]), 
                coeff_b=float(row["coeff_b"]), 
                coeff_c=float(row["coeff_c"]),
                level2_delta_t=int(row["level2_delta_t"])
            )

            # 2. 동적 그룹핑(하위 구역 분할) 쿼리 실행
            c.execute("""
                WITH centers AS (
                    SELECT 
                        sensor_id, sensor_rl_id,
                        CAST(TRIM(point_x) AS numeric) AS center_lon,
                        CAST(TRIM(point_y) AS numeric) AS center_lat,
                        'GROUP_' || LPAD(ROW_NUMBER() OVER(ORDER BY sensor_rl_id)::text, 2, '0') AS new_group_id
                    FROM iot_sensor_ms
                    WHERE TRIM(sensor_category) = %s 
                      AND TRIM(sensor_element_type) = %s
                      AND resource_id = %s
                ),
                distances AS (
                    SELECT 
                        s.sensor_id,
                        s.sensor_rl_id,
                        s.sensor_name,
                        s.sensor_category,
                        s.sensor_element_type,
                        c.new_group_id,
                        ROW_NUMBER() OVER(
                            PARTITION BY s.sensor_rl_id, s.sensor_element_type 
                            ORDER BY SQRT(POWER(CAST(TRIM(s.point_x) AS numeric) - c.center_lon, 2) + POWER(CAST(TRIM(s.point_y) AS numeric) - c.center_lat, 2)) ASC
                        ) as rn
                    FROM iot_sensor_ms s
                    CROSS JOIN centers c
                    WHERE TRIM(s.sensor_category) IN (%s, %s)
                      AND s.point_x IS NOT NULL 
                      AND s.point_y IS NOT NULL
                      AND s.resource_id = %s
                )
                SELECT 
                    new_group_id AS group_id,
                    sensor_id,
                    sensor_rl_id,
                    sensor_name,
                    TRIM(sensor_category) AS sensor_category,
                    TRIM(sensor_element_type) AS sensor_element_type
                FROM distances
                WHERE rn = 1;
            """, (
                settings.COND_EXT_TEMP_CAT, settings.COND_EXT_TEMP_TYPE, resource_id,
                settings.COND_EXT_TEMP_CAT, settings.COND_WALL_TEMP_CAT, resource_id
            ))
            
            sensors = c.fetchall()
            for s in sensors:
                gid = s["group_id"]
                cat = s["sensor_category"]
                el_type = s["sensor_element_type"]
                sid = s["sensor_id"] 
                sname = s["sensor_name"]
                srid = s["sensor_rl_id"] 

                
                if not sid: continue

                # 1. 💡 센서 상세 정보는 맵(Map)에 등록 (srid를 Key로 사용)
                if srid not in config.sensor_info_map:
                    config.sensor_info_map[srid] = {
                        "sid": sid,
                        "sname": sname,
                        "el_type": el_type
                    }

                if gid not in config.groups:
                    config.groups[gid] = CondensationGroup(group_id=gid)
                
                if cat == settings.COND_WALL_TEMP_CAT and el_type == settings.COND_WALL_TEMP_TYPE:
                    config.groups[gid].wall_temp_sensor_ids.append(srid)
                elif cat == settings.COND_EXT_TEMP_CAT and el_type == settings.COND_EXT_TEMP_TYPE:
                    config.groups[gid].ext_temp_sensor_ids.append(srid)
                elif cat == settings.COND_EXT_HUMID_CAT and el_type == settings.COND_EXT_HUMID_TYPE:
                    config.groups[gid].ext_humid_sensor_ids.append(srid)

            # 3. anomaly_thresholds 테이블에서 외부 온/습도 임계값 동적 조회
            c.execute("""
                SELECT sensor_element_type, alert_level, threshold_value
                FROM anomaly_thresholds
                WHERE resource_id = %s
                  AND sensor_category IN (%s, %s)
                  AND sensor_element_type IN (%s, %s)
                  AND is_active = true
            """, (
                resource_id, 
                settings.COND_EXT_TEMP_CAT, settings.COND_EXT_HUMID_CAT,
                settings.COND_EXT_TEMP_TYPE, settings.COND_EXT_HUMID_TYPE
            ))
            
            thresholds = c.fetchall()
            for th in thresholds:
                el_type = th["sensor_element_type"]
                lvl_str = str(th["alert_level"]).upper()
                val = float(th["threshold_value"])
                
                if el_type == settings.COND_EXT_TEMP_TYPE and lvl_str == 'LEVEL_1':
                    config.ext_temp_l1_threshold = val
                elif el_type == settings.COND_EXT_HUMID_TYPE:
                    if lvl_str == 'LEVEL_1':
                        config.ext_humid_l1_threshold = val
                    elif lvl_str == 'LEVEL_2':
                        config.ext_humid_l2_threshold = val

            return config

    # ── 침수 파라미터 조회 ────────────────────────────────────────

    def get_flood_config(self, resource_id: str) -> Optional[FloodConfig]:
        with self._cur() as c:
            cfg = FloodConfig(resource_id=resource_id)

            # 1. 해당 구역의 배수설비(SC000025) - 펌프(SE000017) 센서 목록 조회
            c.execute("""
                SELECT ism.sensor_id,ism.sensor_rl_id, ism.sensor_name ,ism.sensor_element_type
                FROM iot_sensor_ms ism
                JOIN iot_sensor_resource_rl isrr ON ism.sensor_id = isrr.sensor_id
                WHERE isrr.resource_id = %s
                  AND ism.sensor_category = %s
                  AND ism.sensor_element_type = %s
                  AND ism.sensor_rl_id IS NOT NULL
            """, (resource_id, settings.FLOOD_CAT, settings.FLOOD_PUMP_TYPE))
            
            rows = c.fetchall()
            if not rows:
                return None

            for s in rows:
                srid = s["sensor_rl_id"]
                if not srid: continue

                cfg.sensor_rl_ids.append(srid)

                 # 1. 💡 센서 상세 정보는 맵(Map)에 등록 (srid를 Key로 사용)
                if srid not in cfg.sensor_info_map:
                    cfg.sensor_info_map[srid] = {
                        "sid": s["sensor_id"],
                        "sname": s["sensor_name"],
                        "el_type": s["sensor_element_type"]
                    }

            # 2. anomaly_flood_parameters 테이블에서 기준 파라미터 조회
            c.execute("""
                SELECT inlet_pipe_height_mm, level3_offset_mm
                FROM anomaly_flood_parameters
                WHERE resource_id = %s
            """, (resource_id,))
            
            param = c.fetchone()
            if param:
                if param.get("inlet_pipe_height_mm") is not None:
                    cfg.inlet_pipe_height_mm = float(param["inlet_pipe_height_mm"])
                if param.get("level3_offset_mm") is not None:
                    cfg.level3_offset_mm = float(param["level3_offset_mm"])

            return cfg

    def get_flood_current_status(self, sensor_rl_id: str) -> dict:
        """device_current_status 테이블에서 배수 펌프의 최신 JSON 데이터를 파싱하여 반환합니다."""
        with self._cur() as c:
            c.execute("""
                SELECT normalized_fields
                FROM device_current_status
                WHERE sensor_network_uid = %s
                AND cmd='0xA9'
            """, (sensor_rl_id,))
            
            row = c.fetchone()
            if row and row.get("normalized_fields"):
                import json
                fields = row["normalized_fields"]
                # DB 설정에 따라 dict로 바로 반환되거나 문자열일 수 있으므로 처리
                return json.loads(fields) if isinstance(fields, str) else fields
            return {}


     # ── 화재_가스────────────────────────────────────────

    def get_fire_gas_config(self, resource_id: str) -> Optional[FireGasConfig]:
        with self._cur() as c:
            config = FireGasConfig(resource_id=resource_id)
            
            # 1. 해당 구역의 복합가스센서(SC000009) 목록 조회 ("sensor_rl_id-채널" 혹은 활용 방식에 맞게)
            # 여기서는 sensor_rl_id와 sensor_element_type을 결합하여 InfluxDB 조회용 키를 만든다고 가정
            c.execute("""
                SELECT ism.sensor_id,ism.sensor_rl_id, ism.sensor_name,ism.sensor_element_type
                FROM iot_sensor_ms ism, iot_sensor_resource_rl isrr , iot_resource_ms irm 
                WHERE ism.sensor_id  = isrr.sensor_id 
                AND isrr.resource_id = irm.resource_id 
                AND irm.resource_id =  %s
                AND ism.sensor_category = %s
                AND ism.sensor_rl_id IS NOT NULL
            """, (resource_id, settings.FIRE_GAS_CAT))
            
            sensors = c.fetchall()
            if not sensors:
                return None
                
            # 센서 ID와 엘리먼트 타입을 묶어서 저장 (InfluxDB에서 조회 시 구분용)
            # 예: "1-201" 형태로 저장하거나, 별도 매핑 규칙 사용
            for s in sensors:
                srid = s["sensor_rl_id"]
                if not srid: continue
                config.sensor_ids.append(srid)

                # 1. 💡 센서 상세 정보는 맵(Map)에 등록 (srid를 Key로 사용)
                if srid not in config.sensor_info_map:
                    config.sensor_info_map[srid] = {
                        "sid": s["sensor_id"],
                        "sname": s["sensor_name"],
                        "el_type": s["sensor_element_type"]
                    }


            # 2. anomaly_thresholds 테이블에서 화재/가스 동적 임계값 조회
            c.execute("""
                SELECT sensor_element_type, alert_level, threshold_value
                FROM anomaly_thresholds
                WHERE resource_id = %s
                  AND sensor_category = %s
                  AND is_active = true
            """, (resource_id, settings.FIRE_GAS_CAT))
            
            thresholds = c.fetchall()
            for th in thresholds:
                el_type = th["sensor_element_type"]
                lvl_str = str(th["alert_level"]).upper()
                val = float(th["threshold_value"])
                
                # 온도 임계값 매핑 (온도는 분당 상승과 절대온도가 혼재하므로 정책에 따라 1, 2, 3 매핑)
                if el_type == settings.FIRE_GAS_TEMP_TYPE:
                    if lvl_str == 'LEVEL_1': config.temp_l1_rise_threshold = val
                    elif lvl_str == 'LEVEL_2': config.temp_l2_threshold = val
                    elif lvl_str == 'LEVEL_3': config.temp_l3_threshold = val
                
                # 가스별 임계값 매핑
                elif el_type == settings.FIRE_GAS_O2_TYPE:
                    if lvl_str == 'LEVEL_1': config.o2_l1_threshold = val
                    elif lvl_str == 'LEVEL_2': config.o2_l2_threshold = val
                    elif lvl_str == 'LEVEL_3': config.o2_l3_threshold = val
                elif el_type == settings.FIRE_GAS_CO_TYPE:
                    if lvl_str == 'LEVEL_1': config.co_l1_threshold = val
                    elif lvl_str == 'LEVEL_2': config.co_l2_threshold = val
                    elif lvl_str == 'LEVEL_3': config.co_l3_threshold = val
                elif el_type == settings.FIRE_GAS_CO2_TYPE:
                    if lvl_str == 'LEVEL_1': config.co2_l1_threshold = val
                    elif lvl_str == 'LEVEL_2': config.co2_l2_threshold = val
                    elif lvl_str == 'LEVEL_3': config.co2_l3_threshold = val
                elif el_type == settings.FIRE_GAS_H2S_TYPE:
                    if lvl_str == 'LEVEL_1': config.h2s_l1_threshold = val
                    elif lvl_str == 'LEVEL_2': config.h2s_l2_threshold = val
                    elif lvl_str == 'LEVEL_3': config.h2s_l3_threshold = val

            return config

    # ── 구조물 ────────────────────────────────────────
    def get_structure_config(self, resource_id: str) -> Optional[StructureConfig]:
        with self._cur() as c:
            config = StructureConfig(resource_id=resource_id)
            
            # 1. 해당 구역의 균열/진동 센서 목록 조회
            c.execute("""
                SELECT ism.sensor_id,ism.sensor_rl_id, ism.sensor_name ,ism.sensor_element_type
                FROM iot_sensor_ms ism
                JOIN iot_sensor_resource_rl isrr ON ism.sensor_id = isrr.sensor_id
                WHERE isrr.resource_id = %s
                  AND ism.sensor_category IN (%s, %s)
                  AND ism.sensor_rl_id IS NOT NULL
            """, (resource_id, settings.STRUCTURE_CRACK_CAT, settings.STRUCTURE_VIB_CAT))
            
            sensors = c.fetchall()
            if not sensors:
                return None
                
            for s in sensors:
                # InfluxDB 조회용 키 생성 (예: "70-449-SE000001")
                srid=s["sensor_rl_id"]
                config.sensor_ids.append(srid)

                # 1. 💡 센서 상세 정보는 맵(Map)에 등록 (srid를 Key로 사용)
                if srid not in config.sensor_info_map:
                    config.sensor_info_map[srid] = {
                        "sid": s["sensor_id"],
                        "sname": s["sensor_name"],
                        "el_type": s["sensor_element_type"]
                    }

            # 2. anomaly_thresholds 테이블에서 구조물 동적 임계값 조회
            c.execute("""
                SELECT sensor_element_type, alert_level, threshold_value
                FROM anomaly_thresholds
                WHERE resource_id = %s
                  AND sensor_category IN (%s, %s)
                  AND is_active = true
            """, (resource_id, settings.STRUCTURE_CRACK_CAT, settings.STRUCTURE_VIB_CAT))
            
            thresholds = c.fetchall()
            for th in thresholds:
                el_type = th["sensor_element_type"]
                lvl_str = str(th["alert_level"]).upper()
                val = float(th["threshold_value"])
                
                # 균열(Crack) 임계값 매핑
                if el_type == settings.STRUCTURE_CRACK_TYPE:
                    if lvl_str == 'LEVEL_1': config.crack_l1_threshold = val
                    elif lvl_str == 'LEVEL_2': config.crack_l2_threshold = val
                    elif lvl_str == 'LEVEL_3': config.crack_l3_threshold = val
                
                # 진동(Vibration) 임계값 매핑
                elif el_type == settings.STRUCTURE_VIB_TYPE:
                    if lvl_str == 'LEVEL_1': config.vib_l1_threshold = val
                    elif lvl_str == 'LEVEL_2': config.vib_l2_threshold = val
                    elif lvl_str == 'LEVEL_3': config.vib_l3_threshold = val

            return config
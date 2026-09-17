"""
infra/influx_client.py
───────────────────────
InfluxDB Flux 쿼리 클라이언트.

v2.1 변경:
- get_condensation_data_multi(): 센서 ID 목록을 받아
  한 번의 Flux 쿼리로 여러 센서 데이터를 조회.
  반환: {sensor_id: {"wall_temp": float, "humidity": float, ...}}
"""
from __future__ import annotations
import json
from datetime import datetime, UTC
from typing import Optional

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.exceptions import InfluxDBError
from influxdb_client.client.write_api import SYNCHRONOUS

from config.settings import settings
from utils.logger import get_logger

log = get_logger(__name__)


class InfluxRepo:
    def __init__(self) -> None:
        self._client = InfluxDBClient(
            url=settings.influx.url, token=settings.influx.token,
            org=settings.influx.org, timeout=30_000,
        )
        self._qapi   = self._client.query_api()
        self._bucket = settings.influx.bucket
        self._org = settings.influx.org

    def _query(self, flux: str) -> list[dict]:
        try:
            tables = self._qapi.query(flux, org=self._org)
            return [r.values for t in tables for r in t.records]
        except InfluxDBError as e:
            log.error("InfluxDB 쿼리 실패: %s", e)
            return []
        except Exception as e:
            log.error("InfluxDB 예외: %s", e)
            return []
        
    def _parse_sensor_id(self, original_id: str) -> tuple[str, str]:
        """
        '센서고유ID|센서연동ID|센서타입ID|센스명' 형태의 문자열에서 
        가운데 '1-201'을 찾아 (sensor_id, channel_id) 튜플로 반환합니다.
        """
        # '/'가 포함되어 있다면 가운데 요소 추출, 아니면 원본 그대로 사용
        target_str = original_id.split('|')[1] if '|' in original_id else original_id
        
        parts = target_str.split('-')
        if len(parts) == 2:
            return parts[0], parts[1]
        return None, None
    
    def _parse_sensor_channel_id(self, original_id: str) -> tuple[str, str]:
           
            parts = original_id.split('-')
            if len(parts) == 2:
                return parts[0], parts[1]
            return None, None
    
    @staticmethod
    def _pick(rows: list[dict], field: str, agg: str = "last") -> Optional[float]:
        vals = [r["_value"] for r in rows
                if r.get("_field") == field and r["_value"] is not None]
        if not vals:
            return None
        if agg == "mean": return sum(vals) / len(vals)
        if agg == "max":  return max(vals)
        if agg == "min":  return min(vals)
        return vals[-1]

    # ==========================================
    # 2. 화재/가스 엔진용 데이터 조회 (분당 상승률 계산 포함)
    # ==========================================
    # def get_fire_gas_data(self, sensor_ids: list[str]) -> dict[str, dict[str, float]]:
    #     if not sensor_ids:
    #         return {}

    #     conditions = []
    #     reverse_map = {}

    #     for s_rlid in sensor_ids:
    #         s_id, c_id = self._parse_sensor_channel_id(s_rlid)
    #         if s_id and c_id:
    #             conditions.append(f'(r["sensor_id"] == "{s_id}" and r["channel_id"] == "{c_id}")')
    #             reverse_map[f"{s_id}-{c_id}"] = s_rlid

    #     if not conditions:
    #         return {}

    #     filter_str = " or ".join(conditions)

    #     # 분당 온도 상승률 계산을 위해 최근 3분 치의 시계열 데이터를 모두 가져옴
    #     query = f'''
    #         from(bucket: "{self._bucket}")
    #           |> range(start: -3m)
    #           |> filter(fn: (r) => r["_measurement"] == "sensor_pf")
    #           |> filter(fn: (r) => r["_field"] == "calc_value")
    #           |> filter(fn: (r) => {filter_str})
    #     '''

    #     raw_data = {}
    #     try:
    #         tables = self._qapi.query(query, org=self._org)
    #         for table in tables:
    #             for record in table.records:
    #                 s_id = record.values.get("sensor_id")
    #                 c_id = record.values.get("channel_id")
    #                 val = record.get_value()
    #                 time_val = record.get_time() # datetime 객체 반환
                    
    #                 if s_id and c_id and val is not None:
    #                     key = f"{s_id}-{c_id}"
    #                     original_key = reverse_map.get(key)
                        
    #                     if original_key:
    #                         if original_key not in raw_data:
    #                             raw_data[original_key] = []
    #                         raw_data[original_key].append((time_val, float(val)))
    #     except Exception as e:
    #         log.error(f"화재/가스 센서 InfluxDB 조회 실패: {e}")

    #     results = {}
    #     for key, values in raw_data.items():
    #         if not values:
    #             continue
                
    #         # 시간순(오름차순) 정렬
    #         values.sort(key=lambda x: x[0])
            
    #         current_time, current_val = values[-1]
    #         rise_per_min = 0.0

    #         # 데이터가 2개 이상일 때만 1분 전과 비교하여 상승률 계산
    #         if len(values) > 1:
    #             past_val = values[0][1]
    #             past_time = values[0][0]
                
    #             # 역순으로 탐색하며 현재로부터 가장 '1분 전'에 가까운 데이터를 찾음
    #             for t, v in reversed(values[:-1]):
    #                 dt_seconds = (current_time - t).total_seconds()
    #                 if dt_seconds >= 60:
    #                     past_val = v
    #                     past_time = t
    #                     break
                
    #             time_diff_min = (current_time - past_time).total_seconds() / 60.0
    #             if time_diff_min > 0:
    #                 rise_per_min = (current_val - past_val) / time_diff_min

    #         results[key] = {
    #             "current": current_val,
    #             "rise_per_min": round(rise_per_min, 2)
    #         }

    #     return results
    def get_fire_gas_data(self, sensor_ids: list[str]) -> dict[str, dict[str, float]]:
        if not sensor_ids:
            return {}

        s_ids_set = set()
        reverse_map = {}

        # 1. 고유한 sensor_id만 추출하고, 파이썬 단에서 2차 검증할 역방향 맵 생성
        for s_rlid in sensor_ids:
            s_id, c_id = self._parse_sensor_channel_id(s_rlid)
            if s_id and c_id:
                s_ids_set.add(s_id)
                reverse_map[f"{s_id}-{c_id}"] = s_rlid

        if not s_ids_set:
            return {}

        # 2. 파이썬 set을 InfluxDB 배열 문자열 형태로 변환 (예: '["10", "11", ...]')
        #flux_array_str = json.dumps(list(s_ids_set))
        regex_str = "^(" + "|".join(s_ids_set) + ")$"

        # 3. or 체이닝을 없애고, contains() 함수로 초고속 1차 필터링 쿼리 생성
        query = f'''
            from(bucket: "{self._bucket}")
              |> range(start: -3m)
              |> filter(fn: (r) => r["_measurement"] == "sensor_pf")
              |> filter(fn: (r) => r["_field"] == "calc_value")
              |> filter(fn: (r) => r["sensor_id"] =~ /{regex_str}/)
        '''

        raw_data = {}
        try:
            tables = self._qapi.query(query, org=self._org)
            for table in tables:
                for record in table.records:
                    s_id = record.values.get("sensor_id")
                    c_id = record.values.get("channel_id")
                    val = record.get_value()
                    time_val = record.get_time() # datetime 객체 반환
                    
                    if s_id and c_id and val is not None:
                        key = f"{s_id}-{c_id}"
                        # 4. 파이썬 단에서 channel_id까지 정확히 일치하는지 2차 필터링
                        original_key = reverse_map.get(key)
                        
                        if original_key:
                            if original_key not in raw_data:
                                raw_data[original_key] = []
                            raw_data[original_key].append((time_val, float(val)))
        except Exception as e:
            # log 객체를 사용하고 계시다면 기존대로 사용 (logger_name 확인)
            log.error(f"화재/가스 센서 InfluxDB 조회 실패: {e}")

        results = {}
        for key, values in raw_data.items():
            if not values:
                continue
                
            # 시간순(오름차순) 정렬
            values.sort(key=lambda x: x[0])
            
            current_time, current_val = values[-1]
            rise_per_min = 0.0

            # 데이터가 2개 이상일 때만 1분 전과 비교하여 상승률 계산
            if len(values) > 1:
                past_val = values[0][1]
                past_time = values[0][0]
                
                # 역순으로 탐색하며 현재로부터 가장 '1분 전'에 가까운 데이터를 찾음
                for t, v in reversed(values[:-1]):
                    dt_seconds = (current_time - t).total_seconds()
                    if dt_seconds >= 60:
                        past_val = v
                        past_time = t
                        break
                
                time_diff_min = (current_time - past_time).total_seconds() / 60.0
                if time_diff_min > 0:
                    rise_per_min = (current_val - past_val) / time_diff_min

            results[key] = {
                "current": current_val,
                "rise_per_min": round(rise_per_min, 2)
            }

        return results

    # ── 침수 ─────────────────────────────────────────────────────
    def get_flood_data(self, resource_id: str) -> dict[str, Optional[float]]:
        flux = f"""
from(bucket:"{self._bucket}")
  |> range(start:-5m)
  |> filter(fn:(r) => r["resource_id"]=="{resource_id}")
  |> filter(fn:(r) => r["_field"]=="water_level"
         or r["_field"]=="inflow_rate" or r["_field"]=="drain_rate")
  |> mean()"""
        rows = self._query(flux)
        rise = self._get_water_rise(resource_id)
        return {
            "water_level": self._pick(rows, "water_level"),
            "inflow_rate": self._pick(rows, "inflow_rate"),
            "drain_rate":  self._pick(rows, "drain_rate"),
            "rise_rate":   rise,
        }

    def _get_water_rise(self, resource_id: str) -> Optional[float]:
        flux = f"""
from(bucket:"{self._bucket}")
  |> range(start:-2m)
  |> filter(fn:(r) => r["resource_id"]=="{resource_id}"
         and r["_field"]=="water_level")
  |> derivative(unit:1m, nonNegative:false)
  |> last()"""
        return self._pick(self._query(flux), "water_level")

    # ── 결로 (단일 센서 — 폴백용) ─────────────────────────────────
    def get_condensation_data(self, resource_id: str) -> dict[str, Optional[float]]:
        """
        sensor_id 구분 없이 resource_id 태그만으로 조회.
        anomaly_sensors 테이블 미존재 시 폴백으로 사용.
        """
        flux = f"""
from(bucket:"{self._bucket}")
  |> range(start:-10m)
  |> filter(fn:(r) => r["resource_id"]=="{resource_id}")
  |> filter(fn:(r) => r["_field"]=="wall_temp"
         or r["_field"]=="ext_temperature"
         or r["_field"]=="humidity")
  |> last()"""
        rows = self._query(flux)
        return {
            "wall_temp":       self._pick(rows, "wall_temp"),
            "ext_temperature": self._pick(rows, "ext_temperature"),
            "humidity":        self._pick(rows, "humidity"),
        }

    # ── 결로 (다중 센서) ──────────────────────────────────────────
    # def get_condensation_data_multi(
    #     self, 
    #     all_wall_ids: list[str], 
    #     all_ext_temp_ids: list[str], 
    #     all_ext_humid_ids: list[str]
    # ) -> dict[str, dict[str, float]]:
    #     """
    #     다수의 결로 센서(벽체 온도, 외부 온도, 외부 습도) 데이터를 InfluxDB에서 한 번에 조회합니다.
    #     입력 ID 형식: "sensor_id-channel_id" (예: "20-122")
    #     """
        
    #     # 1. 모든 고유 센서 ID 취합 (InfluxDB에 한 번만 쿼리하기 위해 중복 제거)
    #     all_ids = set(all_wall_ids + all_ext_temp_ids + all_ext_humid_ids)
    #     if not all_ids:
    #         return {"wall_temps": {}, "ext_temps": {}, "humidities": {}}

    #     # 2. InfluxDB Flux 쿼리 필터 조건 동적 생성
    #     conditions = []
    #     for combined_id in all_ids:
    #         parts = combined_id.split('-')
    #         if len(parts) == 2:
    #             s_id, c_id = parts
    #             # _measurement=sensor_pf 안에서 sensor_id와 channel_id 태그를 동시 만족하는 조건
    #             conditions.append(f'(r["sensor_id"] == "{s_id}" and r["channel_id"] == "{c_id}")')
        
    #     if not conditions:
    #         return {"wall_temps": {}, "ext_temps": {}, "humidities": {}}

    #     filter_str = " or ".join(conditions)

    #     # 3. Flux 쿼리 작성 (최근 15분 데이터 중 각 센서의 마지막 calc_value 값 조회)
    #     query = f'''
    #         from(bucket: "{self._bucket}")
    #           |> range(start: -15m)
    #           |> filter(fn: (r) => r["_measurement"] == "sensor_pf")
    #           |> filter(fn: (r) => r["_field"] == "calc_value")
    #           |> filter(fn: (r) => {filter_str})
    #           |> last()
    #     '''

    #     # 4. InfluxDB 조회 및 결과 딕셔너리 매핑
    #     results_map = {}
    #     try:
    #         tables = self._qapi.query(query, org=self._org)
    #         for table in tables:
    #             for record in table.records:
    #                 # InfluxDB 결과에서 태그 및 필드 값 추출
    #                 s_id = record.values.get("sensor_id")
    #                 c_id = record.values.get("channel_id")
    #                 val = record.get_value()
                    
    #                 if s_id and c_id and val is not None:
    #                     # 원본 입력 형태("20-122")로 다시 조립하여 저장
    #                     combined_key = f"{s_id}-{c_id}"
    #                     results_map[combined_key] = float(val)
    #     except Exception as e:
    #         self.log.error(f"결로 센서 InfluxDB 다중 조회 실패: {e}")

    #     # 5. 조회된 전체 결과를 용도별 리스트에 맞게 분배하여 반환
    #     return {
    #         "wall_temps": {k: results_map[k] for k in all_wall_ids if k in results_map},
    #         "ext_temps": {k: results_map[k] for k in all_ext_temp_ids if k in results_map},
    #         "humidities": {k: results_map[k] for k in all_ext_humid_ids if k in results_map}
    #     }
    
    def get_condensation_data_multi(
        self, 
        all_wall_ids: list[str], 
        all_ext_temp_ids: list[str], 
        all_ext_humid_ids: list[str]
    ) -> dict[str, dict[str, float]]:
        """
        다수의 결로 센서(벽체 온도, 외부 온도, 외부 습도) 데이터를 InfluxDB에서 한 번에 조회합니다.
        입력 ID 형식: "sensor_id-channel_id" (예: "20-122")
        """
        
        # 1. 모든 고유 센서 ID 취합 (InfluxDB에 한 번만 쿼리하기 위해 중복 제거)
        all_ids = set(all_wall_ids + all_ext_temp_ids + all_ext_humid_ids)
        if not all_ids:
            return {"wall_temps": {}, "ext_temps": {}, "humidities": {}}

        # 2. 💡 최적화: sensor_id만 추출하여 정규식(Regex) 문자열 생성
        s_ids_set = set()
        for combined_id in all_ids:
            parts = combined_id.split('-')
            if len(parts) == 2:
                s_ids_set.add(parts[0])
                
        if not s_ids_set:
            return {"wall_temps": {}, "ext_temps": {}, "humidities": {}}
            
        # 예: "^(20|21|22)$" 형태로 변환
        regex_str = "^(" + "|".join(s_ids_set) + ")$"

        # 3. 💡 최적화: Flux 쿼리 작성 (or 체이닝 제거 및 정규식 =~ 사용)
        query = f'''
            from(bucket: "{self._bucket}")
              |> range(start: -15m)
              |> filter(fn: (r) => r["_measurement"] == "sensor_pf")
              |> filter(fn: (r) => r["_field"] == "calc_value")
              |> filter(fn: (r) => r["sensor_id"] =~ /{regex_str}/)
              |> last()
        '''

        # 4. InfluxDB 조회 및 결과 딕셔너리 매핑
        results_map = {}
        try:
            # 환경에 따라 self._qapi 인지 self._qapi 인지 확인하여 사용하세요
            tables =  self._qapi.query(query, org=self._org)
            for table in tables:
                for record in table.records:
                    # InfluxDB 결과에서 태그 및 필드 값 추출
                    s_id = record.values.get("sensor_id")
                    c_id = record.values.get("channel_id")
                    val = record.get_value()
                    
                    if s_id and c_id and val is not None:
                        combined_key = f"{s_id}-{c_id}"
                        # 💡 2차 검증: 요청한 "sensor_id-channel_id" 조합이 맞는지 파이썬에서 확인
                        if combined_key in all_ids:
                            results_map[combined_key] = float(val)
        except Exception as e:
            log.error(f"결로 센서 InfluxDB 다중 조회 실패: {e}")

        # 5. 조회된 전체 결과를 용도별 리스트에 맞게 분배하여 반환
        return {
            "wall_temps": {k: results_map[k] for k in all_wall_ids if k in results_map},
            "ext_temps": {k: results_map[k] for k in all_ext_temp_ids if k in results_map},
            "humidities": {k: results_map[k] for k in all_ext_humid_ids if k in results_map}
        }

    # ==========================================
    # 4. 구조물(균열/진동) 엔진용 데이터 조회
    # ==========================================
    # def get_structure_data(self, sensor_ids: list[str]) -> dict[str, dict[str, float]]:
    #     """
    #     sensor_ids 예시: ["70-449", 70-449]
       
    #     """
    #     if not sensor_ids:
    #         return {}

    #     conditions = []
    #     reverse_map = {}

    #     for original_id in sensor_ids:
    #         s_id, c_id = self._parse_sensor_channel_id(original_id)

    #         if s_id and c_id:
    #             conditions.append(f'(r["sensor_id"] == "{s_id}" and r["channel_id"] == "{c_id}")')
    #             reverse_map[f"{s_id}-{c_id}"] = original_id
        

    #     if not conditions:
    #         return {}

    #     # 여러 센서를 한 번에 조회하기 위한 or 조건 문자열 생성
    #     filter_str = " or ".join(conditions)

    #     # 가장 최근(last) 데이터만 조회
    #     query = f'''
    #         from(bucket: "{self._bucket}")
    #           |> range(start: -15m)
    #           |> filter(fn: (r) => r["_measurement"] == "sensor_pf")
    #           |> filter(fn: (r) => r["_field"] == "calc_value")
    #           |> filter(fn: (r) => {filter_str})
    #           |> last()
    #     '''

    #     results = {}
    #     try:
    #         tables = self._qapi.query(query, org=self._org)
    #         for table in tables:
    #             for record in table.records:
    #                 s_id = record.values.get("sensor_id")
    #                 c_id = record.values.get("channel_id")
    #                 val = record.get_value()
                    
    #                 if s_id and c_id and val is not None:
    #                     # InfluxDB 결과("70-449")를 조립하여 원본 ID 찾기
    #                     db_key = f"{s_id}-{c_id}"
    #                     original_key = reverse_map.get(db_key)
                        
    #                     if original_key:
    #                         results[original_key] = {
    #                             "current": float(val)
    #                         }
    #     except Exception as e:
    #         log.error(f"구조물 센서 InfluxDB 조회 실패: {e}")

    #     return results

    def get_structure_data(self, sensor_ids: list[str]) -> dict[str, dict[str, float]]:
        """
        sensor_ids 예시: ["70-449", "71-450"]
        """
        if not sensor_ids:
            return {}

        s_ids_set = set()
        reverse_map = {}

        # 1. 고유 sensor_id 추출 및 역방향 매핑 딕셔너리 생성
        for original_id in sensor_ids:
            s_id, c_id = self._parse_sensor_channel_id(original_id)

            if s_id and c_id:
                s_ids_set.add(s_id)
                reverse_map[f"{s_id}-{c_id}"] = original_id
        
        if not s_ids_set:
            return {}

        # 2. 정규식 문자열 생성 (예: "^(70|71|72)$")
        regex_str = "^(" + "|".join(s_ids_set) + ")$"

        # 3. Flux 쿼리 작성 (or 체이닝을 정규식 매칭 `=~` 으로 대체)
        query = f'''
            from(bucket: "{self._bucket}")
              |> range(start: -15m)
              |> filter(fn: (r) => r["_measurement"] == "sensor_pf")
              |> filter(fn: (r) => r["_field"] == "calc_value")
              |> filter(fn: (r) => r["sensor_id"] =~ /{regex_str}/)
              |> last()
        '''

        results = {}
        try:
            tables = self._qapi.query(query, org=self._org)
            for table in tables:
                for record in table.records:
                    s_id = record.values.get("sensor_id")
                    c_id = record.values.get("channel_id")
                    val = record.get_value()
                    
                    if s_id and c_id and val is not None:
                        # 4. 파이썬 단에서 sensor_id와 channel_id 조합이 요청된 ID와 일치하는지 2차 검증
                        db_key = f"{s_id}-{c_id}"
                        original_key = reverse_map.get(db_key)
                        
                        if original_key:
                            results[original_key] = {
                                "current": float(val)
                            }
        except Exception as e:
            # 전역 log 객체 사용 (이전 에러 수정사항 반영)
            log.error(f"구조물 센서 InfluxDB 조회 실패: {e}")

        return results

    # ── 이상 이벤트 기록 ─────────────────────────────────────────
    def write_anomaly_event(
        self, resource_id: str, domain: str, level: str,
        event_type: str, triggered_sensors: list[str],
        sensor_values: dict,
    ) -> None:
        wapi = self._client.write_api(write_options=SYNCHRONOUS)
        pt = (
            Point("anomaly_events")
            .tag("resource_id", resource_id)
            .tag("domain",      domain)
            .tag("level",       level)
            .tag("event_type",  event_type)
            .field("triggered_sensors", ",".join(triggered_sensors))
            .field("sensor_values",
                   json.dumps(sensor_values, ensure_ascii=False))
            .time(datetime.now(UTC))
        )
        try:
            wapi.write(bucket=self._bucket, org=settings.influx.org, record=pt)
        except Exception as e:
            log.error("[%s] 이벤트 기록 실패: %s", resource_id, e)

    def close(self) -> None:
        self._client.close()

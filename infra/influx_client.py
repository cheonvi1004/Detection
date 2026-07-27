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
from datetime import datetime
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
            org=settings.influx.org, timeout=5_000,
        )
        self._qapi   = self._client.query_api()
        self._bucket = settings.influx.bucket

    def _query(self, flux: str) -> list[dict]:
        try:
            tables = self._qapi.query(flux, org=settings.influx.org)
            return [r.values for t in tables for r in t.records]
        except InfluxDBError as e:
            log.error("InfluxDB 쿼리 실패: %s", e)
            return []
        except Exception as e:
            log.error("InfluxDB 예외: %s", e)
            return []

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

    # ── 화재/가스 ─────────────────────────────────────────────────
    def get_fire_gas_data(self, resource_id: str) -> dict[str, Optional[float]]:
        flux = f"""
from(bucket:"{self._bucket}")
  |> range(start:-1m)
  |> filter(fn:(r) => r["resource_id"]=="{resource_id}")
  |> filter(fn:(r) => r["_field"]=="temperature" or r["_field"]=="O2"
         or r["_field"]=="CO" or r["_field"]=="CO2" or r["_field"]=="H2S")
  |> last()"""
        rows = self._query(flux)
        rate = self._get_temp_rate(resource_id)
        return {
            "temperature": self._pick(rows, "temperature"),
            "temp_rate":   rate,
            "O2":          self._pick(rows, "O2"),
            "CO":          self._pick(rows, "CO"),
            "CO2":         self._pick(rows, "CO2"),
            "H2S":         self._pick(rows, "H2S"),
        }

    def _get_temp_rate(self, resource_id: str) -> Optional[float]:
        flux = f"""
from(bucket:"{self._bucket}")
  |> range(start:-2m)
  |> filter(fn:(r) => r["resource_id"]=="{resource_id}"
         and r["_field"]=="temperature")
  |> derivative(unit:1m, nonNegative:false)
  |> last()"""
        return self._pick(self._query(flux), "temperature")

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

        # 2. InfluxDB Flux 쿼리 필터 조건 동적 생성
        conditions = []
        for combined_id in all_ids:
            parts = combined_id.split('-')
            if len(parts) == 2:
                s_id, c_id = parts
                # _measurement=sensor_pf 안에서 sensor_id와 channel_id 태그를 동시 만족하는 조건
                conditions.append(f'(r["sensor_id"] == "{s_id}" and r["channel_id"] == "{c_id}")')
        
        if not conditions:
            return {"wall_temps": {}, "ext_temps": {}, "humidities": {}}

        filter_str = " or ".join(conditions)

        # 3. Flux 쿼리 작성 (최근 15분 데이터 중 각 센서의 마지막 calc_value 값 조회)
        query = f'''
            from(bucket: "{self.bucket}")
              |> range(start: -15m)
              |> filter(fn: (r) => r["_measurement"] == "sensor_pf")
              |> filter(fn: (r) => r["_field"] == "calc_value")
              |> filter(fn: (r) => {filter_str})
              |> last()
        '''

        # 4. InfluxDB 조회 및 결과 딕셔너리 매핑
        results_map = {}
        try:
            tables = self.query_api.query(query, org=self.org)
            for table in tables:
                for record in table.records:
                    # InfluxDB 결과에서 태그 및 필드 값 추출
                    s_id = record.values.get("sensor_id")
                    c_id = record.values.get("channel_id")
                    val = record.get_value()
                    
                    if s_id and c_id and val is not None:
                        # 원본 입력 형태("20-122")로 다시 조립하여 저장
                        combined_key = f"{s_id}-{c_id}"
                        results_map[combined_key] = float(val)
        except Exception as e:
            self.log.error(f"결로 센서 InfluxDB 다중 조회 실패: {e}")

        # 5. 조회된 전체 결과를 용도별 리스트에 맞게 분배하여 반환
        return {
            "wall_temps": {k: results_map[k] for k in all_wall_ids if k in results_map},
            "ext_temps": {k: results_map[k] for k in all_ext_temp_ids if k in results_map},
            "humidities": {k: results_map[k] for k in all_ext_humid_ids if k in results_map}
        }

    # ── 구조 ─────────────────────────────────────────────────────
    def get_structure_data(self, resource_id: str) -> dict[str, Optional[float]]:
        flux_mean = f"""
from(bucket:"{self._bucket}")
  |> range(start:-5m)
  |> filter(fn:(r) => r["resource_id"]=="{resource_id}")
  |> filter(fn:(r) => r["_field"]=="crack_width" or r["_field"]=="strain")
  |> mean()"""
        flux_max = f"""
from(bucket:"{self._bucket}")
  |> range(start:-5m)
  |> filter(fn:(r) => r["resource_id"]=="{resource_id}"
         and r["_field"]=="vibration")
  |> max()"""
        rows_m = self._query(flux_mean)
        rows_x = self._query(flux_max)
        return {
            "crack_width": self._pick(rows_m, "crack_width"),
            "strain":      self._pick(rows_m, "strain"),
            "vibration":   self._pick(rows_x, "vibration"),
        }

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
            .time(datetime.utcnow())
        )
        try:
            wapi.write(bucket=self._bucket, org=settings.influx.org, record=pt)
        except Exception as e:
            log.error("[%s] 이벤트 기록 실패: %s", resource_id, e)

    def close(self) -> None:
        self._client.close()

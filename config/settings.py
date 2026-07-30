"""config/settings.py — 환경변수 기반 전역 설정"""
from __future__ import annotations
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def _i(k, d): return int(os.getenv(k, d))
def _f(k, d): return float(os.getenv(k, d))
def _s(k, d=""): return os.getenv(k, d)

from dataclasses import dataclass

@dataclass(frozen=True)
class InfluxSettings:
    url:    str = _s("INFLUX_URL",    "http://localhost:8086")
    token:  str = _s("INFLUX_TOKEN",  "")
    org:    str = _s("INFLUX_ORG",    "")
    bucket: str = _s("INFLUX_BUCKET", "utility_tunnel")

@dataclass(frozen=True)
class PgSettings:
    host:     str = _s("PG_HOST",     "localhost")
    port:     int = _i("PG_PORT",     5432)
    dbname:   str = _s("PG_DB",       "utility_tunnel")
    user:     str = _s("PG_USER",     "detection_engine")
    password: str = _s("PG_PASSWORD", "")
    schema:   str = _s("PG_SCHEMA",   "sysmaster")   # 최종 DDL 스키마

    @property
    def dsn(self) -> str:
        return (f"host={self.host} port={self.port} dbname={self.dbname} "
                f"user={self.user} password={self.password} "
                f"options=-csearch_path%3D{self.schema}")

@dataclass(frozen=True)
class PollSettings:
    fire_gas_sec:     int = _i("POLL_FIRE_GAS_SEC",     10)
    flood_sec:        int = _i("POLL_FLOOD_SEC",         30)
    condensation_sec: int = _i("POLL_CONDENSATION_SEC", 300)
    structure_sec:    int = _i("POLL_STRUCTURE_SEC",     60)

@dataclass(frozen=True)
class DebounceSettings:
    level1_sec: int = _i("DEBOUNCE_LEVEL1_SEC", 30)
    level2_sec: int = _i("DEBOUNCE_LEVEL2_SEC", 30)
    level3_sec: int = _i("DEBOUNCE_LEVEL3_SEC", 10)
    level4_sec: int = _i("DEBOUNCE_LEVEL4_SEC",  0)

    def get(self, level: int) -> int:
        return {1: self.level1_sec, 2: self.level2_sec,
                3: self.level3_sec, 4: self.level4_sec}.get(level, 0)

@dataclass(frozen=True)
class AlertSettings:
    cooldown_sec: int = _i("ALERT_COOLDOWN_SEC", 300)
    sms_url:      str = _s("SMS_GATEWAY_URL")
    sms_api_key:  str = _s("SMS_API_KEY")
    push_url:     str = _s("PUSH_SERVER_URL")
    push_key:     str = _s("PUSH_SERVER_KEY")

@dataclass(frozen=True)
class Settings:
    influx:    InfluxSettings   = InfluxSettings()
    pg:        PgSettings       = PgSettings()
    poll:      PollSettings     = PollSettings()
    debounce:  DebounceSettings = DebounceSettings()
    alert:     AlertSettings    = AlertSettings()
    log_level: str  = _s("LOG_LEVEL", "INFO")
    log_file:  str  = _s("LOG_FILE",  "logs/anomaly_detection.log")
    # 침수 L1: 수위 급상승 감지 기준 (mm/min)
    rapid_rise_threshold_mm_per_min: float = _f("RAPID_RISE_THRESHOLD", 20.0)

    # ==========================================
    # 결로 감지 대상 센서 매핑 코드 설정
    # ==========================================
    # 외부 온도 센서
    COND_EXT_TEMP_CAT = os.getenv("COND_EXT_TEMP_CAT", "SC000011")
    COND_EXT_TEMP_TYPE = os.getenv("COND_EXT_TEMP_TYPE", "SE000019")
    
    # 외부 습도 센서
    COND_EXT_HUMID_CAT = os.getenv("COND_EXT_HUMID_CAT", "SC000011")
    COND_EXT_HUMID_TYPE = os.getenv("COND_EXT_HUMID_TYPE", "SE000020")
    
    # 벽체 온도 센서
    COND_WALL_TEMP_CAT = os.getenv("COND_WALL_TEMP_CAT", "SC000006")
    COND_WALL_TEMP_TYPE = os.getenv("COND_WALL_TEMP_TYPE", "SE000009")

    # ==========================================
    # 화재/가스 감지 대상 센서 매핑 코드 설정
    # ==========================================
    FIRE_GAS_CAT = os.getenv("FIRE_GAS_CAT", "SC000009")
    
    FIRE_GAS_O2_TYPE = os.getenv("FIRE_GAS_O2_TYPE", "SE000012")   # O2
    FIRE_GAS_CO_TYPE = os.getenv("FIRE_GAS_CO_TYPE", "SE000013")   # CO
    FIRE_GAS_CO2_TYPE = os.getenv("FIRE_GAS_CO2_TYPE", "SE000014") # CO2
    FIRE_GAS_H2S_TYPE = os.getenv("FIRE_GAS_H2S_TYPE", "SE000015") # H2S
    FIRE_GAS_TEMP_TYPE = os.getenv("FIRE_GAS_TEMP_TYPE", "SE000031") # 온도
    

    # ==========================================
    # 침수/배수 감지 대상 센서 매핑 코드 설정
    # ==========================================
    FLOOD_CAT = os.getenv("FLOOD_CAT", "SC000025")
    FLOOD_PUMP_TYPE = os.getenv("FLOOD_PUMP_TYPE", "SE000017")

    # ==========================================
    # 구조물(균열/진동) 감지 대상 센서 매핑 코드 설정
    # ==========================================
    STRUCTURE_CRACK_CAT = os.getenv("STRUCTURE_CRACK_CAT", "SC000001")
    STRUCTURE_VIB_CAT = os.getenv("STRUCTURE_VIB_CAT", "SC000004")
    
    STRUCTURE_CRACK_TYPE = os.getenv("STRUCTURE_CRACK_TYPE", "SE000001")
    STRUCTURE_VIB_TYPE = os.getenv("STRUCTURE_VIB_TYPE", "SE000005")

    # ==========================================
    # 이벤트 중계 API 설정
    # ==========================================
    EVENT_API_URL = os.getenv("EVENT_API_URL", "https://kong.spaasta.com/event-api")
    EVENT_API_AUTH = os.getenv("EVENT_API_AUTH", "Basic YWRtaW46Y29ucGl0YTc3ISE=")

settings = Settings()

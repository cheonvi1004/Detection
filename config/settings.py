"""config/settings.py"""
from __future__ import annotations
import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _int(k, d): return int(os.getenv(k, d))
def _flt(k, d): return float(os.getenv(k, d))
def _str(k, d=""): return os.getenv(k, d)


@dataclass(frozen=True)
class InfluxSettings:
    url:    str = _str("INFLUX_URL",    "http://localhost:8086")
    token:  str = _str("INFLUX_TOKEN",  "")
    org:    str = _str("INFLUX_ORG",    "")
    bucket: str = _str("INFLUX_BUCKET", "utility_tunnel")


@dataclass(frozen=True)
class PgSettings:
    host:     str = _str("PG_HOST",     "localhost")
    port:     int = _int("PG_PORT",     5432)
    dbname:   str = _str("PG_DB",       "utility_tunnel")
    user:     str = _str("PG_USER",     "detection_engine")
    password: str = _str("PG_PASSWORD", "")

    @property
    def dsn(self) -> str:
        return (f"host={self.host} port={self.port} dbname={self.dbname} "
                f"user={self.user} password={self.password}")


@dataclass(frozen=True)
class PollSettings:
    fire_gas_sec:     int = _int("POLL_FIRE_GAS_SEC",     10)
    flood_sec:        int = _int("POLL_FLOOD_SEC",         30)
    condensation_sec: int = _int("POLL_CONDENSATION_SEC", 300)
    structure_sec:    int = _int("POLL_STRUCTURE_SEC",     60)


@dataclass(frozen=True)
class DebounceSettings:
    level1_sec: int = _int("DEBOUNCE_LEVEL1_SEC", 30)
    level2_sec: int = _int("DEBOUNCE_LEVEL2_SEC", 30)
    level3_sec: int = _int("DEBOUNCE_LEVEL3_SEC", 10)
    level4_sec: int = _int("DEBOUNCE_LEVEL4_SEC",  0)

    def get(self, level: int) -> int:
        return {1: self.level1_sec, 2: self.level2_sec,
                3: self.level3_sec, 4: self.level4_sec}.get(level, 0)


@dataclass(frozen=True)
class AlertSettings:
    cooldown_sec: int = _int("ALERT_COOLDOWN_SEC", 300)
    sms_url:      str = _str("SMS_GATEWAY_URL")
    sms_api_key:  str = _str("SMS_API_KEY")
    push_url:     str = _str("PUSH_SERVER_URL")
    push_key:     str = _str("PUSH_SERVER_KEY")


@dataclass(frozen=True)
class Settings:
    influx:   InfluxSettings   = InfluxSettings()
    pg:       PgSettings       = PgSettings()
    poll:     PollSettings     = PollSettings()
    debounce: DebounceSettings = DebounceSettings()
    alert:    AlertSettings    = AlertSettings()
    log_level: str  = _str("LOG_LEVEL", "INFO")
    log_file:  str  = _str("LOG_FILE",  "logs/anomaly_detection.log")
    rapid_rise_threshold_mm_per_min: float = _flt("RAPID_RISE_THRESHOLD", 20.0)


settings = Settings()

"""infra/pg_client.py — PostgreSQL 설정값 조회"""
from __future__ import annotations
from contextlib import contextmanager
from typing import Optional

import psycopg2, psycopg2.extras
from config.settings import settings
from domain.models import CondensationConfig, FloodConfig
from utils.logger import get_logger

log = get_logger(__name__)


class PgRepo:
    def __init__(self) -> None:
        self._conn = None

    def connect(self) -> None:
        self._conn = psycopg2.connect(settings.pg.dsn)
        self._conn.autocommit = True
        log.info("PostgreSQL 연결 완료")

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

    def get_active_zones(self) -> list[dict]:
        with self._cur() as c:
            c.execute("SELECT zone_id, zone_name, has_ventilation, has_drainage "
                      "FROM zones WHERE is_active = TRUE")
            return [dict(r) for r in c.fetchall()]

    def get_condensation_config(self, zone_id: str) -> Optional[CondensationConfig]:
        with self._cur() as c:
            c.execute("""
                SELECT zone_id, has_ventilation,
                       coeff_a, coeff_b, coeff_c,
                       level1_delta_t, level2_delta_t,
                       season_winter_max_c, season_spring_max_c,
                       target_temp_winter_c, target_temp_spring_c, target_temp_summer_c,
                       target_rh_winter_pct, target_rh_spring_pct, target_rh_summer_pct
                FROM v_condensation_config WHERE zone_id = %s
            """, (zone_id,))
            row = c.fetchone()
        if not row:
            log.warning("결로 설정 없음: zone_id=%s", zone_id)
            return None
        return CondensationConfig(
            zone_id=zone_id,
            coeff_a=float(row["coeff_a"]), coeff_b=float(row["coeff_b"]),
            coeff_c=float(row["coeff_c"]),
            level1_delta_t=float(row["level1_delta_t"]),
            level2_delta_t=float(row["level2_delta_t"]),
            season_winter_max_c=float(row["season_winter_max_c"]),
            season_spring_max_c=float(row["season_spring_max_c"]),
            target_temp_winter=float(row["target_temp_winter_c"]),
            target_temp_spring=float(row["target_temp_spring_c"]),
            target_temp_summer=float(row["target_temp_summer_c"]),
            target_rh_winter=float(row["target_rh_winter_pct"]),
            target_rh_spring=float(row["target_rh_spring_pct"]),
            target_rh_summer=float(row["target_rh_summer_pct"]),
            has_ventilation=bool(row["has_ventilation"]),
        )

    def get_flood_config(self, zone_id: str) -> Optional[FloodConfig]:
        with self._cur() as c:
            c.execute("""
                SELECT zone_id, level2_trigger_mm, level3_trigger_mm,
                       level4_inflow_threshold_lpm, config_status, is_verified
                FROM v_flood_config WHERE zone_id = %s
            """, (zone_id,))
            row = c.fetchone()
        if not row:
            log.warning("침수 파라미터 없음: zone_id=%s", zone_id)
            return None
        def _f(v): return float(v) if v is not None else None
        return FloodConfig(
            zone_id=zone_id,
            level2_trigger_mm=_f(row["level2_trigger_mm"]),
            level3_trigger_mm=_f(row["level3_trigger_mm"]),
            level4_inflow_threshold_lpm=_f(row["level4_inflow_threshold_lpm"]),
            config_status=row["config_status"],
            is_verified=bool(row["is_verified"]),
        )

    # def get_thresholds(self, zone_id: str, domain: str) -> list[dict]:
    #     with self._cur() as c:
    #         c.execute("""
    #             SELECT t.sensor_type_id, t.alert_level, t.operator,
    #                    t.threshold_value, t.aggregation, t.eval_window_sec,
    #                    t.composite_group, t.composite_min_count
    #             FROM thresholds t
    #             JOIN threshold_profiles p  ON p.profile_id     = t.profile_id
    #             JOIN sensor_types       st ON st.sensor_type_id = t.sensor_type_id
    #             LEFT JOIN zone_threshold_profiles zp ON zp.zone_id = %s
    #             WHERE st.domain = %s
    #               AND t.is_active = TRUE AND p.is_active = TRUE
    #               AND (t.effective_to IS NULL OR t.effective_to > NOW())
    #               AND t.profile_id = COALESCE(zp.profile_id,
    #                   (SELECT profile_id FROM threshold_profiles
    #                    WHERE is_default=TRUE LIMIT 1))
    #             ORDER BY t.alert_level, t.sensor_type_id
    #         """, (zone_id, domain))
    #         return [dict(r) for r in c.fetchall()]


    def get_thresholds(self) -> list[dict]:
        with self._cur() as c:
            c.execute("""
                select a.threshold_value, a."alert_level", a."operator", a.description, b.sensor_element_type, a.sensor_category, b.sensor_element_name 
                from anomaly_thresholds a,
                     iot_sensor_element_type b
                where a.sensor_category = b.sensor_category
                and   a.sensor_element_type = b.sensor_element_type
                order by a.sensor_element_type, a.sensor_category, a."alert_level"
            """)
            return [dict(r) for r in c.fetchall()]

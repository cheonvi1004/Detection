"""
infra/pg_client.py
───────────────────
PostgreSQL sysmaster 스키마 조회 클라이언트.

최종 DDL 기준:
- anomaly_thresholds:        resource_id + sensor_category + sensor_element_type
- anomaly_condensation_thresholds: resource_id + coeff_id + level2_delta_t
- anomaly_condensation_formula_coefficients: a, b, c 계수
- anomaly_flood_parameters:  resource_id 기반 물리 파라미터
"""
from __future__ import annotations
from contextlib import contextmanager
from typing import Optional

import psycopg2, psycopg2.extras
from config.settings import settings
from domain.models import CondensationConfig, FloodConfig, ThresholdRow
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
        """
        활성 구역(resource) 목록.
        resource 마스터 테이블이 없는 경우 anomaly_flood_parameters의
        resource_id를 union하여 전체 구역 목록을 구성.
        """
        with self._cur() as c:
            c.execute("""
                SELECT DISTINCT resource_id
                FROM (
                    SELECT resource_id FROM anomaly_thresholds WHERE is_active = TRUE
                    UNION
                    SELECT resource_id FROM anomaly_flood_parameters
                    UNION
                    SELECT resource_id FROM anomaly_condensation_thresholds
                    WHERE resource_id IS NOT NULL
                ) r
                ORDER BY resource_id
            """)
            return [dict(r) for r in c.fetchall()]

    # ── 일반 임계값 조회 (화재/가스, 구조) ───────────────────────
    def get_thresholds(self, resource_id: str, sensor_category: str) -> list[ThresholdRow]:
        """
        anomaly_thresholds에서 resource_id + sensor_category 기준 임계값 조회.
        resource_id가 없으면 DEFAULT 구역(NULL 또는 'DEFAULT') 값 사용.
        """
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

        result = []
        seen = set()
        for r in rows:
            key = (r["sensor_element_type"], r["alert_level"])
            if key in seen:
                continue   # resource_id 우선 적용 후 DEFAULT는 스킵
            seen.add(key)

            ctl = r.get("composite_target_level")
            result.append(ThresholdRow(
                resource_id          = r["resource_id"] or "DEFAULT",
                sensor_category      = r["sensor_category"],
                sensor_element_type  = r["sensor_element_type"],
                alert_level          = AlertLevel[r["alert_level"]] if isinstance(r["alert_level"], str)
                                       else AlertLevel(int(r["alert_level"])),
                operator             = ThresholdOp(r["operator"]),
                threshold_value      = float(r["threshold_value"]),
                aggregation          = AggregationFn(r["aggregation"]),
                eval_window_sec      = int(r["eval_window_sec"]),
                composite_group      = r.get("composite_group"),
                composite_min_count  = int(r.get("composite_min_count") or 1),
                composite_target_level = AlertLevel[ctl] if ctl else None,
                description          = r.get("description"),
                is_active            = bool(r["is_active"]),
            ))
        return result

    # ── 결로 설정 조회 ────────────────────────────────────────────
    def get_condensation_config(self, resource_id: str) -> Optional[CondensationConfig]:
        """
        anomaly_condensation_thresholds + anomaly_condensation_formula_coefficients 조인.
        resource_id 전용 설정 없으면 기본값(resource_id IS NULL) 적용.
        """
        with self._cur() as c:
            c.execute("""
                SELECT
                    ct.resource_id, ct.level2_delta_t,
                    fc.a, fc.b, fc.c
                FROM anomaly_condensation_thresholds ct
                JOIN anomaly_condensation_formula_coefficients fc
                    ON fc.coeff_id = ct.coeff_id
                WHERE ct.resource_id = %s
                   OR ct.resource_id IS NULL
                ORDER BY
                    CASE WHEN ct.resource_id = %s THEN 0 ELSE 1 END
                LIMIT 1
            """, (resource_id, resource_id))
            row = c.fetchone()

        if not row:
            log.warning("결로 설정 없음: resource_id=%s", resource_id)
            return None

        return CondensationConfig(
            resource_id      = resource_id,
            coeff_a          = float(row["a"]),
            coeff_b          = float(row["b"]),
            coeff_c          = float(row["c"]),
            level2_delta_t   = float(row["level2_delta_t"]),
        )

    # ── 침수 파라미터 조회 ────────────────────────────────────────
    def get_flood_config(self, resource_id: str) -> Optional[FloodConfig]:
        """anomaly_flood_parameters에서 resource_id 기준 조회."""
        with self._cur() as c:
            c.execute("""
                SELECT
                    resource_id,
                    inlet_pipe_height_mm,
                    level3_offset_mm,
                    pump_count,
                    pump_capacity_lpm,
                    pump_total_capacity_lpm,
                    drain_disabled_margin_pct,
                    is_verified,
                    note
                FROM anomaly_flood_parameters
                WHERE resource_id = %s
            """, (resource_id,))
            row = c.fetchone()

        if not row:
            log.warning("침수 파라미터 없음: resource_id=%s", resource_id)
            return None

        def _f(v): return float(v) if v is not None else None
        def _i(v): return int(v)   if v is not None else None

        return FloodConfig(
            resource_id                = resource_id,
            inlet_pipe_height_mm       = _f(row["inlet_pipe_height_mm"]),
            level3_offset_mm           = float(row["level3_offset_mm"]),
            pump_count                 = _i(row["pump_count"]),
            pump_capacity_lpm          = _f(row["pump_capacity_lpm"]),
            pump_total_capacity_lpm    = _f(row["pump_total_capacity_lpm"]),
            drain_disabled_margin_pct  = float(row["drain_disabled_margin_pct"]),
            is_verified                = bool(row["is_verified"]),
            note                       = row.get("note"),
        )

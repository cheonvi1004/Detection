"""
domain/models.py
─────────────────
최종 DDL 기준 도메인 모델.

v2.1 변경:
- SensorMeta: 구역에 등록된 개별 센서 정보
- CondensationSensorResult: 센서 1개의 결로 계산 결과
- CondensationConfig: level1_delta_t property 자동 산출 유지
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .enums import AlertLevel, DetectionDomain, ThresholdOp, AggregationFn


# ── 센서 메타 ─────────────────────────────────────────────────────
@dataclass
class SensorMeta:
    """
    구역에 등록된 개별 센서 정보.
    PostgreSQL anomaly_sensors 테이블(또는 동등한 테이블) 1행 대응.
    """
    sensor_id:    str            # InfluxDB tag 값 (예: "COND-A01")
    resource_id:  str            # 소속 구역
    sensor_type:  str            # 측정 항목 ("wall_temp", "humidity" 등)
    location_desc: Optional[str] = None   # 센서 설치 위치 설명 (예: "북측 벽체")
    is_active:    bool = True


# ── 결로 센서별 계산 결과 ────────────────────────────────────────
@dataclass
class CondensationSensorResult:
    """
    센서 1개에 대한 결로 계산 결과.
    Worst-case 선정 및 상세 로그에 활용.
    """
    sensor_id:   str
    t_dry:       float            # 건구온도 (℃)
    rh:          float            # 상대습도 (%)
    t_dew:       float            # 노점온도 (℃)
    delta_t:     float            # ΔT = T_dry − T_dew
    level:       AlertLevel
    detail:      str
    t_ext:       Optional[float] = None   # 외기온도 (℃)


# ── 일반 임계값 행 ────────────────────────────────────────────────
@dataclass
class ThresholdRow:
    resource_id:           str
    sensor_category:       str
    sensor_element_type:   str
    alert_level:           AlertLevel
    operator:              ThresholdOp
    threshold_value:       float
    aggregation:           AggregationFn
    eval_window_sec:       int
    composite_group:       Optional[str]
    composite_min_count:   int
    composite_target_level: Optional[AlertLevel]
    description:           Optional[str]
    is_active:             bool


# ── 영역별 평가 결과 ──────────────────────────────────────────────
@dataclass
class DomainResult:
    resource_id:       str
    domain:            DetectionDomain
    level:             AlertLevel
    triggered_sensors: list[str]        = field(default_factory=list)
    sensor_values:     dict[str, float] = field(default_factory=dict)
    evaluated_at:      datetime         = field(default_factory=datetime.utcnow)
    detail:            str              = ""
    # 결로 전용: 센서별 상세 결과 (Worst-case 포함)
    sensor_results:    list[CondensationSensorResult] = field(default_factory=list)


# ── 구역 종합 상태 ────────────────────────────────────────────────
@dataclass
class ZoneStatus:
    resource_id:    str
    zone_level:     AlertLevel
    domain_results: dict[DetectionDomain, DomainResult]
    previous_level: AlertLevel = AlertLevel.NONE
    evaluated_at:   datetime   = field(default_factory=datetime.utcnow)

    @property
    def escalated(self) -> bool:
        return self.zone_level > self.previous_level

    @property
    def recovered(self) -> bool:
        return self.zone_level < self.previous_level


# ── 결로 감지 설정 ────────────────────────────────────────────────
@dataclass
class CondensationConfig:
    """
    anomaly_condensation_thresholds + formula_coefficients 조인 결과.
    level1_delta_t: DB에 없음 → level2_delta_t × 5/3 자동 산출.
    """
    resource_id:         str
    coeff_a:             float
    coeff_b:             float
    coeff_c:             float
    level2_delta_t:      float    # 주의 ΔT 상한 Y (℃)
    has_ventilation:     bool = True
    season_winter_max_c: float = 12.0
    season_spring_max_c: float = 23.0
    target_temp_winter:  float = 20.0
    target_temp_spring:  float = 22.5
    target_temp_summer:  float = 25.0
    target_rh_winter:    float = 60.0
    target_rh_spring:    float = 65.0
    target_rh_summer:    float = 70.0

    @property
    def level1_delta_t(self) -> float:
        """관심 ΔT 상한 X = Y × 5/3 (예: Y=3.0 → X=5.0)"""
        return round(self.level2_delta_t * 5.0 / 3.0, 2)


# ── 침수 감지 설정 ────────────────────────────────────────────────
@dataclass
class FloodConfig:
    resource_id:               str
    inlet_pipe_height_mm:      Optional[float]
    level3_offset_mm:          float         = 150.0
    pump_count:                Optional[int]  = None
    pump_capacity_lpm:         Optional[float] = None
    pump_total_capacity_lpm:   Optional[float] = None
    drain_disabled_margin_pct: float          = 10.0
    is_verified:               bool           = False
    note:                      Optional[str]  = None

    @property
    def is_configurable(self) -> bool:
        return self.inlet_pipe_height_mm is not None

    @property
    def level2_trigger_mm(self) -> Optional[float]:
        return self.inlet_pipe_height_mm

    @property
    def level3_trigger_mm(self) -> Optional[float]:
        if self.inlet_pipe_height_mm is None:
            return None
        return self.inlet_pipe_height_mm + self.level3_offset_mm

    @property
    def level4_inflow_threshold_lpm(self) -> Optional[float]:
        if self.pump_total_capacity_lpm is None:
            return None
        return self.pump_total_capacity_lpm * (1 + self.drain_disabled_margin_pct / 100.0)

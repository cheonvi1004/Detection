"""
domain/models.py
─────────────────
최종 DDL 기준 도메인 모델.

주요 변경:
- resource_id 사용 (zone_id 대신)
- ThresholdRow: sensor_category + sensor_element_type 2컬럼 구조
- CondensationConfig: level2_delta_t만 존재 (level1은 없음)
- FloodConfig: drain_disabled_margin_pct 포함
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .enums import AlertLevel, DetectionDomain, ThresholdOp, AggregationFn


@dataclass
class ThresholdRow:
    """
    anomaly_thresholds 테이블 1행 표현.
    sensor_category: 감지 영역 (FIRE_GAS / FLOOD / STRUCTURE)
    sensor_element_type: 센서 종류 (TEMP_ABSOLUTE / GAS_CO 등)
    """
    resource_id:          str
    sensor_category:      str            # DetectionDomain 값
    sensor_element_type:  str            # influx field key 대응
    alert_level:          AlertLevel
    operator:             ThresholdOp
    threshold_value:      float
    aggregation:          AggregationFn
    eval_window_sec:      int
    composite_group:      Optional[str]
    composite_min_count:  int
    composite_target_level: Optional[AlertLevel]
    description:          Optional[str]
    is_active:            bool


@dataclass
class DomainResult:
    """영역별 평가 결과."""
    resource_id:       str
    domain:            DetectionDomain
    level:             AlertLevel
    triggered_sensors: list[str]        = field(default_factory=list)
    sensor_values:     dict[str, float] = field(default_factory=dict)
    evaluated_at:      datetime         = field(default_factory=datetime.utcnow)
    detail:            str              = ""


@dataclass
class ZoneStatus:
    """구역 종합 상태."""
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


@dataclass
class CondensationConfig:
    """
    anomaly_condensation_thresholds + anomaly_condensation_formula_coefficients 조인 결과.
    level1_delta_t 없음 — DDL에 level2_delta_t만 정의됨.
    경계(L3): ΔT ≤ 0 (결로 발생) → 고정
    주의(L2): ΔT ≤ level2_delta_t
    관심(L1): level2_delta_t < ΔT ≤ (level2_delta_t + margin) — 엔진에서 계산
    """
    resource_id:         str
    coeff_a:             float    # Magnus-Tetens a (Pa)
    coeff_b:             float    # b
    coeff_c:             float    # c (℃)
    level2_delta_t:      float    # 주의 ΔT 상한 Y (℃)
    has_ventilation:     bool = True

    # 계절별 기준 (환기 설비 제어용)
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
        """
        관심 단계 ΔT 상한 X.
        DDL에 명시 없음 → 주의 임계값(Y)의 1.67배를 기본값으로 사용.
        예: Y=3.0 → X=5.0
        """
        return round(self.level2_delta_t * 5.0 / 3.0, 2)


@dataclass
class FloodConfig:
    """anomaly_flood_parameters 테이블 1행 표현."""
    resource_id:                 str
    inlet_pipe_height_mm:        Optional[float]
    level3_offset_mm:            float           = 150.0
    pump_count:                  Optional[int]   = None
    pump_capacity_lpm:           Optional[float] = None
    pump_total_capacity_lpm:     Optional[float] = None   # 생성 컬럼
    drain_disabled_margin_pct:   float           = 10.0
    is_verified:                 bool            = False
    note:                        Optional[str]   = None

    @property
    def is_configurable(self) -> bool:
        return self.inlet_pipe_height_mm is not None

    @property
    def level2_trigger_mm(self) -> Optional[float]:
        if self.inlet_pipe_height_mm is None:
            return None
        return self.inlet_pipe_height_mm

    @property
    def level3_trigger_mm(self) -> Optional[float]:
        if self.inlet_pipe_height_mm is None:
            return None
        return self.inlet_pipe_height_mm + self.level3_offset_mm

    @property
    def level4_inflow_threshold_lpm(self) -> Optional[float]:
        """L4 판정 기준: 배수량 × (1 + margin%)"""
        if self.pump_total_capacity_lpm is None:
            return None
        return self.pump_total_capacity_lpm * (1 + self.drain_disabled_margin_pct / 100.0)

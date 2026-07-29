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
class CondensationGroup:
    group_id: str
    wall_temp_sensor_ids: list[str] = field(default_factory=list)
    ext_temp_sensor_ids: list[str] = field(default_factory=list)
    ext_humid_sensor_ids: list[str] = field(default_factory=list)

@dataclass
class CondensationConfig:
    resource_id:             str
    coeff_a:             float
    coeff_b:             float
    coeff_c:             float
    level2_delta_t:      int
    
    # 하위 구역(그룹) 딕셔너리
    groups: dict[str, CondensationGroup] = field(default_factory=dict)
    
    # 흐름도 평가용 외부 온습도 임계값 (DB 조회 실패 시 사용할 기본값)
    ext_temp_l1_threshold:  float = 30.0  # 관심 단계: 외기온도 30℃ 이상
    ext_humid_l1_threshold: float = 60.0  # 관심 단계: 상대습도 60% 이상
    ext_humid_l2_threshold: float = 75.0  # 주의 단계: 상대습도 75% 이상

    #@property
    #def level1_delta_t(self) -> float:
    #    """관심 ΔT 상한 X = Y × 5/3 (예: Y=3.0 → X=5.0)"""
    #    return round(self.level2_delta_t * 5.0 / 3.0, 2)


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



@dataclass
class FireGasConfig:
    resource_id: str
    sensor_ids: list[str] = field(default_factory=list) # "sensor_id-channel_id" 리스트
    
    # 온도 임계값 (기본값 설정)
    temp_l1_rise_threshold: float = 8.0  # 분당 상승(관심)
    temp_l2_threshold: float = 60.0      # 절대온도(주의)
    temp_l3_threshold: float = 75.0      # 절대온도(경계)

    # 산소(O2) 임계값 (※ 산소는 이하일 때 위험)
    o2_l1_threshold: float = 15.0
    o2_l2_threshold: float = 10.0
    o2_l3_threshold: float = 8.0

    # 일산화탄소(CO) 임계값
    co_l1_threshold: float = 1400.0
    co_l2_threshold: float = 2000.0
    co_l3_threshold: float = 2500.0

    # 이산화탄소(CO2) 임계값
    co2_l1_threshold: float = 5.0
    co2_l2_threshold: float = 10.0
    co2_l3_threshold: float = 30.0

    # 황화수소(H2S) 임계값
    h2s_l1_threshold: float = 100.0
    h2s_l2_threshold: float = 200.0
    h2s_l3_threshold: float = 500.0

"""domain/models.py"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from .enums import AlertLevel, DetectionDomain


@dataclass
class SensorReading:
    zone_id:     str
    sensor_type: str
    value:       float
    timestamp:   datetime
    is_valid:    bool = True


@dataclass
class DomainResult:
    zone_id:           str
    domain:            DetectionDomain
    level:             AlertLevel
    triggered_sensors: list[str]        = field(default_factory=list)
    sensor_values:     dict[str, float] = field(default_factory=dict)
    evaluated_at:      datetime         = field(default_factory=datetime.utcnow)
    detail:            str              = ""


@dataclass
class ZoneStatus:
    zone_id:        str
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
class AlertEvent:
    zone_id:           str
    domain:            DetectionDomain
    level:             AlertLevel
    event_type:        str
    triggered_sensors: list[str]
    sensor_values:     dict[str, float]
    occurred_at:       datetime   = field(default_factory=datetime.utcnow)
    action_taken:      list[str]  = field(default_factory=list)
    note:              str        = ""


@dataclass
class CondensationConfig:
    zone_id:             str
    coeff_a:             float
    coeff_b:             float
    coeff_c:             float
    level1_delta_t:      float
    level2_delta_t:      float
    season_winter_max_c: float
    season_spring_max_c: float
    target_temp_winter:  float
    target_temp_spring:  float
    target_temp_summer:  float
    target_rh_winter:    float
    target_rh_spring:    float
    target_rh_summer:    float
    has_ventilation:     bool = True


@dataclass
class FloodConfig:
    zone_id:                     str
    level2_trigger_mm:           Optional[float]
    level3_trigger_mm:           Optional[float]
    level4_inflow_threshold_lpm: Optional[float]
    config_status:               str
    is_verified:                 bool = False

    @property
    def is_configurable(self) -> bool:
        return self.level2_trigger_mm is not None

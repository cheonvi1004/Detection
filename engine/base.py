"""engine/base.py — 감지 엔진 추상 기반"""
from __future__ import annotations
from abc import ABC, abstractmethod
from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult
from utils.logger import get_logger


class BaseDetectionEngine(ABC):
    domain: DetectionDomain

    def __init__(self, influx, pg) -> None:
        self.influx = influx
        self.pg     = pg
        self.log    = get_logger(f"engine.{self.domain.value.lower()}")

    @abstractmethod
    def evaluate(self, zone_id: str) -> DomainResult: ...

    def _safe_evaluate(self, zone_id: str) -> DomainResult:
        try:
            return self.evaluate(zone_id)
        except Exception as e:
            self.log.error("[%s] %s 평가 중 예외: %s", zone_id, self.domain.value, e)
            return DomainResult(zone_id=zone_id, domain=self.domain,
                                level=AlertLevel.NONE, detail=f"평가 오류: {e}")

    def _missing_sensor(self, zone_id: str, sensor: str) -> DomainResult:
        self.log.warning("[%s] 센서 결측: %s", zone_id, sensor)
        return DomainResult(zone_id=zone_id, domain=self.domain,
                            level=AlertLevel.NONE, detail=f"센서 결측: {sensor}")

    def _cap_level(self, level: AlertLevel) -> AlertLevel:
        return min(level, self.domain.max_level)

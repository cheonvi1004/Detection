"""
engine/base.py
───────────────
감지 엔진 추상 기반 클래스.
"""
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
    def evaluate(self, resource_id: str) -> DomainResult: ...

    def _safe_evaluate(self, resource_id: str) -> DomainResult:
        try:
            return self.evaluate(resource_id)
        except Exception as e:
            self.log.error("[%s] %s 평가 예외: %s", resource_id, self.domain.value, e)
            return DomainResult(resource_id=resource_id, domain=self.domain,
                                level=AlertLevel.NONE, detail=f"평가 오류: {e}")

    def _missing(self, resource_id: str, sensor: str) -> DomainResult:
        self.log.warning("[%s] 센서 결측: %s", resource_id, sensor)
        return DomainResult(resource_id=resource_id, domain=self.domain,
                            level=AlertLevel.NONE, detail=f"센서 결측: {sensor}")

    def _cap(self, level: AlertLevel) -> AlertLevel:
        """영역별 최대 단계 제한 (결로: LEVEL_3)."""
        return min(level, self.domain.max_level)

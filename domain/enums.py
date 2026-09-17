"""
domain/enums.py
────────────────
최종 DDL(sysmaster) 기준 열거형.

주요 변경:
- ThresholdOp, AggregationFn: DDL ENUM 타입과 1:1 매핑
- 결로(CONDENSATION): 심각(LEVEL_4) 미적용 → max_level = LEVEL_3
"""
from enum import Enum, IntEnum


class AlertLevel(IntEnum):
    """이상 단계 (비교 연산 가능하도록 IntEnum)."""
    NONE    = 0
    LEVEL_1 = 1   # 관심
    LEVEL_2 = 2   # 주의
    LEVEL_3 = 3   # 경계
    LEVEL_4 = 4   # 심각 (결로 미적용)

    @property
    def label(self) -> str:
        return {0: "정상", 1: "관심", 2: "주의", 3: "경계", 4: "심각"}[self.value]

    @property
    def pg_value(self) -> str:
        """PostgreSQL alert_level ENUM 값"""
        return f"LEVEL_{self.value}" if self.value > 0 else "NONE"


class DetectionDomain(str, Enum):
    """감지 영역 — DDL sensor_category 필드와 대응."""
    FIRE_GAS     = "FIRE_GAS"
    FLOOD        = "FLOOD"
    CONDENSATION = "CONDENSATION"
    STRUCTURE    = "STRUCTURE"

    @property
    def max_level(self) -> "AlertLevel":
        """결로는 경계(L3)까지만 유효."""
        if self == DetectionDomain.CONDENSATION:
            return AlertLevel.LEVEL_3
        return AlertLevel.LEVEL_4


class ThresholdOp(str, Enum):
    """비교 연산자 — DDL threshold_op ENUM."""
    GTE = "GTE"   # >=
    LTE = "LTE"   # <=
    GT  = "GT"    # >
    LT  = "LT"    # <

    def evaluate(self, value: float, threshold: float) -> bool:
        return {
            "GTE": value >= threshold,
            "LTE": value <= threshold,
            "GT":  value >  threshold,
            "LT":  value <  threshold,
        }[self.value]


class AggregationFn(str, Enum):
    """집계 함수 — DDL aggregation_fn ENUM."""
    LAST       = "LAST"
    MEAN       = "MEAN"
    MAX        = "MAX"
    DERIVATIVE = "DERIVATIVE"   # 변화율 (온도 상승률 등)


class Season(str, Enum):
    WINTER      = "winter"
    SPRING_FALL = "spring_fall"
    SUMMER      = "summer"

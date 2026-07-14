"""
domain/enums.py
"""
from enum import Enum, IntEnum


class AlertLevel(IntEnum):
    NONE    = 0
    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3
    LEVEL_4 = 4

    @property
    def label(self) -> str:
        return {0: "정상", 1: "관심", 2: "주의", 3: "경계", 4: "심각"}[self.value]


class DetectionDomain(str, Enum):
    FIRE_GAS      = "FIRE_GAS"
    FLOOD         = "FLOOD"
    CONDENSATION  = "CONDENSATION"
    STRUCTURE     = "STRUCTURE"

    @property
    def max_level(self) -> "AlertLevel":
        if self == DetectionDomain.CONDENSATION:
            return AlertLevel.LEVEL_3
        return AlertLevel.LEVEL_4


class ThresholdOp(str, Enum):
    GTE = "GTE"
    LTE = "LTE"
    GT  = "GT"
    LT  = "LT"

    def evaluate(self, value: float, threshold: float) -> bool:
        return {"GTE": value >= threshold, "LTE": value <= threshold,
                "GT": value > threshold,  "LT": value < threshold}[self.value]


class Season(str, Enum):
    WINTER      = "winter"
    SPRING_FALL = "spring_fall"
    SUMMER      = "summer"

class SensorCategory(str, Enum):
    FIRE         = "SC000012"
    GAS          = "SC000009"
    CONDENSATION = "SC000011"
    STRUCTURE    = "SC000001"

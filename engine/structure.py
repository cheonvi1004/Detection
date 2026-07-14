"""engine/structure.py — 구조 안전 감지 엔진"""
from __future__ import annotations
from domain.enums import AlertLevel, DetectionDomain
from domain.models import DomainResult
from engine.base import BaseDetectionEngine

_CRACK     = [(AlertLevel.LEVEL_4,0.5),(AlertLevel.LEVEL_3,0.3),(AlertLevel.LEVEL_2,0.1)]
_STRAIN    = [(AlertLevel.LEVEL_4,3000.0),(AlertLevel.LEVEL_3,2500.0),(AlertLevel.LEVEL_2,2000.0)]
_VIBRATION = [(AlertLevel.LEVEL_4,1.0),(AlertLevel.LEVEL_3,0.5),(AlertLevel.LEVEL_2,0.2)]

def _gte(v,rules): return next((l for l,t in rules if v>=t), AlertLevel.NONE)
def _gt(v,rules):  return next((l for l,t in rules if v>t),  AlertLevel.NONE)


class StructureEngine(BaseDetectionEngine):
    domain = DetectionDomain.STRUCTURE

    def evaluate(self, zone_id: str) -> DomainResult:
        data = self.influx.get_structure_data(zone_id)
        sv, sl = {}, {}

        crack = data.get("crack_width")
        if crack is not None:
            sv["crack_width"] = crack; sl["CRACK_WIDTH"] = _gte(crack, _CRACK)

        # strain = data.get("strain") # 변형률 (제외)
        # if strain is not None:
        #     sv["strain"] = strain; sl["STRAIN"] = _gt(strain, _STRAIN)

        vib = data.get("vibration")
        if vib is not None:
            sv["vibration"] = vib; sl["VIBRATION"] = _gte(vib, _VIBRATION)

        if not sl:
            return self._missing_sensor(zone_id, "ALL")

        l3 = [s for s,lv in sl.items() if lv >= AlertLevel.LEVEL_3]
        if len(l3) >= 2:
            final  = AlertLevel.LEVEL_4
            detail = f"복합조건 L3→L4 ({', '.join(l3)})"
        else:
            final  = max(sl.values())
            units  = {"CRACK_WIDTH":"mm","STRAIN":"με","VIBRATION":"cm/s"}
            fields = {"CRACK_WIDTH":"crack_width","STRAIN":"strain","VIBRATION":"vibration"}
            parts  = [f"{s}={sv.get(fields[s],'?')}{units[s]}({lv.label})"
                      for s,lv in sl.items() if lv > AlertLevel.NONE]
            detail = ", ".join(parts) or "정상"

        triggered = [s for s,lv in sl.items() if lv >= AlertLevel.LEVEL_1]
        self.log.debug("[%s] 구조: %s", zone_id, final.label)
        return DomainResult(zone_id=zone_id, domain=self.domain, level=final,
                            triggered_sensors=triggered, sensor_values=sv, detail=detail)

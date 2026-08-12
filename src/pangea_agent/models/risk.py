from __future__ import annotations

from pydantic import BaseModel


class RiskCard(BaseModel):
    risk_id: str
    title: str
    dfx: list[str]
    severity: str
    confidence: str
    trigger: str
    system_result: str
    external_observation: str
    exclusion_condition: str
    translation_status: str

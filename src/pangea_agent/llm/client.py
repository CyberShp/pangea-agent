from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base_url=os.getenv("PANGEA_LLM_BASE_URL", ""),
            api_key=os.getenv("PANGEA_LLM_API_KEY", ""),
            model=os.getenv("PANGEA_LLM_MODEL", ""),
        )

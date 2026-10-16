"""Executor model is inherited from the connected OAW model configuration."""
from dataclasses import dataclass

from .constants import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


@dataclass(frozen=True)
class LLMCard:
    name: str
    model: str
    api_key: str = ""
    base_url: str = ""

    def public_dict(self):
        return {"name": self.name, "model": self.model}


def select_executor_llm_card(**kwargs) -> LLMCard:
    return LLMCard("oaw", LLM_MODEL, LLM_API_KEY, LLM_BASE_URL)

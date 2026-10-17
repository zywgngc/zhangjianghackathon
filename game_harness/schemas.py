from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunRequest(Contract):
    description: str = Field(min_length=5, max_length=8000)
    engine: Literal["auto", "canvas", "dom"] = "auto"


class Brief(Contract):
    title: str = Field(min_length=1, max_length=80)
    rewritten_prompt: str = Field(min_length=20, max_length=6000)
    core_loop: list[str] = Field(min_length=2, max_length=12)
    user_requirements: list[str] = Field(min_length=1, max_length=25)
    system_requirements: list[str] = Field(default_factory=list, max_length=15)
    assumptions: list[str] = Field(max_length=15)
    acceptance_criteria: list[str] = Field(min_length=3, max_length=20)


class Design(Contract):
    engine: Literal["canvas", "dom"]
    engine_reason: str
    mechanics: list[str] = Field(min_length=3, max_length=25)
    controls: list[str] = Field(min_length=1, max_length=12)
    win_condition: str
    lose_condition: str
    visual_style: str
    responsive_strategy: str
    acceptance_criteria: list[str] = Field(min_length=3, max_length=20)
    knowledge_ids: list[str] = Field(max_length=8)


class Defect(Contract):
    severity: Literal["critical", "major", "minor"]
    evidence: str = Field(min_length=1)
    fix: str = Field(min_length=1)


class Review(Contract):
    verdict: Literal["pass", "repair"]
    summary: str
    defects: list[Defect] = Field(max_length=15)
    limitations: list[str] = Field(max_length=15)

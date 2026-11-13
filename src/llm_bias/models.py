from enum import StrEnum

from pydantic import BaseModel, Field


class AnswerType(StrEnum):
    NUMBER = "number"
    WORD = "word"

class RunStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class RunRequest(BaseModel):
    experiment: str
    model: str
    prompts: list[str]
    samples_per_prompt: int
    answer_type: AnswerType | None = None
    name: str | None = None
    temperature: float = 1.0
    top_p: float = 1.0
    max_tokens: int = Field(default=16, gt=0)
    seed: int | None = None
    enable_thinking: bool = False
    reasoning_effort: str | None = None


class Sample(BaseModel):
    run_id: int
    prompt_id: int
    sample_index: int
    raw: str

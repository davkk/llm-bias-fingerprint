import yaml
from pydantic import BaseModel, ConfigDict, Field

from llm_bias import config
from llm_bias.models import AnswerType


class JobParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = 1.0
    top_p: float = 1.0
    seed: int | None = None
    reasoning_effort: str | None = None


class JobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    experiment: str
    answer_type: AnswerType | None = None
    models: list[str] = Field(min_length=1)
    prompts: list[str] = Field(min_length=1)
    samples_per_prompt: int = Field(gt=0)
    params: JobParams = Field(default_factory=JobParams)


def load_job_spec(path: str) -> JobSpec:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return JobSpec.model_validate(raw)


def resolved_config(spec: JobSpec) -> dict:
    return {
        "system_prompt": config.SYSTEM_PROMPT,
        **spec.params.model_dump(),
    }

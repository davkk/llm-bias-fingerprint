# llm-bias-fingerprint

- Research harness for probing LLM behavior and answer bias.
- Samples completions from model variants under identical prompts.
- Stores raw text only; aggregates at read time.
- Quantization bias is one implemented area; harness is generic.
- Jobs declared as YAML, expanded via `llm-bias` CLI.
- Sampling and analysis kept separate.

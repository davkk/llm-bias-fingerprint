# llm-bias-fingerprint feature map

One file per user-facing feature. Every journey starts from the isolated launch
in `../SKILL.md`. Use the scratch database and ports; never the repo `data.db` or
the user's model server on 8013.

| Feature | One line |
| --- | --- |
| [job-run](job-run.md) | Expand a YAML job into one queued run per model with the `llm-bias` CLI. |
| [dashboard](dashboard.md) | Watch queued, running, stalled, done, and failed runs in the live web table. |
| [inspect-runs](inspect-runs.md) | Read run status and raw samples through the CLI and HTTP, and delete a run. |
| [analysis](analysis.md) | Read label and numeric value distributions per run, per model, and overall. |
| [reports](reports.md) | Generate the Plotly HTML report for one experiment or all of them. |

The default proof uses one model (`Qwen3.5-4B-Q4_0`), one prompt, and three
samples. Multi-model and multi-prompt variants are documented in the relevant
feature files.

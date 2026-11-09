import re

from llm_bias.db import db
from llm_bias.models import AnswerType


def normalize_label(raw: str) -> str:
    return " ".join(raw.split()).lower()


def parse_number(text: str) -> int | None:
    m = re.search(r"-?\d[\d,]*", text)
    if not m:
        return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


def parse_value(answer_type: str | None, text: str) -> int | None:
    if answer_type == AnswerType.NUMBER:
        return parse_number(text)
    return None


def get_analysis(experiment: str | None = None, by: str | None = None) -> dict:
    """Label distribution per run, per model, and overall."""
    by_value = by == "value"
    with db() as conn:
        query = """
                select
                    run.id,
                    run.experiment,
                    run.model,
                    run.status,
                    run.answer_type,
                    sample.raw
                from sample
                join run on sample.run_id = run.id
            """
        params: dict[str, str] = {}
        if experiment is not None:
            query += " where run.experiment = :experiment"
            params["experiment"] = experiment
        query += " order by run.id desc"
        rows = conn.execute(query, params).fetchall()

    def sorted_dist(dist: dict[int | str, int]) -> dict[int | str, int]:
        return {k: dist[k] for k in sorted(dist, key=lambda k: (-dist[k], k))}

    run_meta: dict[int, tuple[str, str, int | None]] = {}
    run_dist: dict[int, dict[int | str, int]] = {}
    run_skipped: dict[int, int] = {}
    by_model_raw: dict[str, dict[int | str, int]] = {}
    by_model_skipped: dict[str, int] = {}
    overall_raw: dict[int | str, int] = {}
    overall_skipped = 0
    for row in rows:
        run_id = row["id"]
        run_meta.setdefault(run_id, (row["experiment"], row["model"], row["status"]))
        label = normalize_label(row["raw"])
        value = parse_value(row["answer_type"], row["raw"])
        if by_value and value is None:
            run_dist.setdefault(run_id, {})
            run_skipped[run_id] = run_skipped.get(run_id, 0) + 1
            by_model_raw.setdefault(row["model"], {})
            by_model_skipped[row["model"]] = by_model_skipped.get(row["model"], 0) + 1
            overall_skipped += 1
            continue
        if by_value:
            assert value is not None
            key = value
        else:
            key = label
        dist = run_dist.setdefault(run_id, {})
        dist[key] = dist.get(key, 0) + 1
        model_dist = by_model_raw.setdefault(row["model"], {})
        model_dist[key] = model_dist.get(key, 0) + 1
        overall_raw[key] = overall_raw.get(key, 0) + 1

    by_model = [(sum(dist.values()), model, sorted_dist(dist)) for model, dist in by_model_raw.items()]
    by_model.sort(key=lambda item: (-item[0], item[1]))

    runs = []
    for run_id in sorted(run_dist, reverse=True):
        entry = {
            "run_id": run_id,
            "experiment": run_meta[run_id][0],
            "model": run_meta[run_id][1],
            "status": run_meta[run_id][2],
            "n": sum(run_dist[run_id].values()),
            "dist": sorted_dist(run_dist[run_id]),
        }
        if by_value:
            entry["skipped"] = run_skipped.get(run_id, 0)
        runs.append(entry)

    by_model_out = []
    for n, model, dist in by_model:
        entry = {"model": model, "n": n, "dist": dist}
        if by_value:
            entry["skipped"] = by_model_skipped.get(model, 0)
        by_model_out.append(entry)

    overall = {"n": sum(overall_raw.values()), "dist": sorted_dist(overall_raw)}
    if by_value:
        overall["skipped"] = overall_skipped

    return {
        "runs": runs,
        "by_model": by_model_out,
        "overall": overall,
    }

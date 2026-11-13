"""Generate a self-contained HTML report with Plotly figures for an experiment."""

import html
import math
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from llm_bias import analysis, config
from llm_bias.db import db, init_db


def plane_models(data):
    models = []
    rows = (data or {}).get("by_model") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        entries = []
        dist = row.get("dist") or {}
        if isinstance(dist, dict):
            for label, count in dist.items():
                try:
                    entries.append((str(label), int(count)))
                except (TypeError, ValueError):
                    continue
        if not entries:
            continue
        n = row.get("n")
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = sum(c for _, c in entries)
        if n <= 0:
            n = sum(c for _, c in entries) or 1
        models.append({"model": str(row.get("model", "")), "entries": entries, "n": n})
    models.sort(key=lambda m: m["model"], reverse=True)
    return models


def share(count, model):
    n = model["n"]
    if n and n > 0:
        return count / n
    s = sum(c for _, c in model["entries"])
    return count / s if s > 0 else 0.0


def entropy(model):
    h = 0.0
    for _, count in model["entries"]:
        p = share(count, model)
        if p > 0:
            h -= p * math.log2(p)
    return h


def normalized_entropy(model):
    h = entropy(model)
    k = max(1, len(model["entries"]))
    denom = math.log2(k)
    return h / denom if denom > 0 else 0.0


def top_share(model):
    if not model["entries"]:
        return 0.0
    return share(max(c for _, c in model["entries"]), model)


def js_distance(left, right):
    left_map = dict(left["entries"])
    right_map = dict(right["entries"])
    labels = set(left_map) | set(right_map)
    kl_left = 0.0
    kl_right = 0.0
    for label in labels:
        p = share(left_map.get(label, 0), left)
        q = share(right_map.get(label, 0), right)
        mean = (p + q) / 2.0
        if p > 0 and mean > 0:
            kl_left += p * math.log2(p / mean)
        if q > 0 and mean > 0:
            kl_right += q * math.log2(q / mean)
    js = 0.5 * kl_left + 0.5 * kl_right
    return math.sqrt(max(0.0, js))


DARK = {
    "template": "plotly_dark",
    "paper_bgcolor": "#000000",
    "plot_bgcolor": "#000000",
    "font": {"color": "#e6e9ef", "size": 11, "family": "ui-monospace, SFMono-Regular, Menlo, monospace"},
    "margin": {"l": 50, "r": 20, "t": 30, "b": 40},
}


def fig_distribution_stacked(models):
    counts = {}
    for m in models:
        for label, c in m["entries"]:
            counts[label] = counts.get(label, 0) + c
    labels = [l for l, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    fig = go.Figure()
    for label in labels:
        ys = []
        for m in models:
            d = dict(m["entries"])
            ys.append(share(d.get(label, 0), m))
        fig.add_trace(go.Bar(name=label, x=[m["model"] for m in models], y=ys))
    fig.update_layout(
        barmode="stack",
        title="Label share by model",
        xaxis_title="model",
        yaxis_title="share",
        yaxis={"tickformat": ".0%", "range": [0, 1]},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        **DARK,
    )
    return fig


def fig_heatmap(data, models):
    counts = {}
    overall = (data or {}).get("overall") or {}
    od = overall.get("dist") if isinstance(overall, dict) else None
    if isinstance(od, dict):
        for label, c in od.items():
            counts[str(label)] = counts.get(str(label), 0) + int(c)
    for m in models:
        for label, c in m["entries"]:
            if label not in counts:
                counts[label] = c
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    labels = [l for l, _ in ranked[:12]]
    if labels and all(is_number(l) for l in labels):
        labels_sorted = sorted(labels, key=lambda x: float(x))
    else:
        labels_sorted = sorted(labels)
    z = []
    text = []
    for m in models:
        d = dict(m["entries"])
        row = [share(d.get(label, 0), m) for label in labels_sorted]
        z.append(row)
        text.append([f"{v:.1%}" if v > 0 else "" for v in row])
    fig = go.Figure(
        data=go.Heatmap(
            x=labels_sorted,
            y=[m["model"] for m in models],
            z=z,
            text=text,
            texttemplate="%{text}",
            textfont={"size": 9, "color": "#e6e9ef"},
            colorscale=[[0, "#000000"], [0.05, "#1e3a5f"], [0.2, "#2e5c8a"], [1, "#4c8dff"]],
            zmin=0,
            zmax=max((max(row) for row in z), default=1) or 1,
            hovertemplate="%{y}<br>%{x}: %{z:.1%}<extra></extra>",
        )
    )
    fig.update_layout(title="Label heatmap (share)", xaxis_title="label", yaxis_title="model", **DARK)
    return fig


def is_number(text):
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def fig_pairwise(models):
    n = len(models)
    z = [[0.0] * n for _ in range(n)]
    text = [["" for _ in range(n)] for _ in range(n)]
    for i, a in enumerate(models):
        for j, b in enumerate(models):
            if i == j:
                z[i][j] = 0.0
                text[i][j] = "—"
            else:
                d = js_distance(a, b)
                z[i][j] = d
                text[i][j] = f"{d:.3f}"
    fig = go.Figure(
        data=go.Heatmap(
            x=[m["model"] for m in models],
            y=[m["model"] for m in models],
            z=z,
            text=text,
            texttemplate="%{text}",
            colorscale=[[0, "#000000"], [0.1, "#2a1a3a"], [1, "#bc8cff"]],
            zmin=0,
            zmax=1,
            hovertemplate="%{y}, %{x}: %{z:.3f}<extra></extra>",
        )
    )
    fig.update_layout(title="Pairwise JS distance", **DARK)
    return fig


def fig_rank(models):
    fig = go.Figure()
    for idx, m in enumerate(models):
        entries = sorted(m["entries"], key=lambda kv: (-kv[1], kv[0]))
        entries = sorted(entries, key=lambda kv: (-kv[1], float(kv[0]) if is_number(kv[0]) else float("inf")))
        xs = list(range(1, len(entries) + 1))
        ys = [share(c, m) for _, c in entries]
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines+markers",
                name=m["model"],
                hovertemplate="rank %{x}<br>%{text}: %{y:.1%}<extra></extra>",
                text=[label for label, _ in entries],
            )
        )
    fig.update_layout(
        title="Share by rank (all)",
        xaxis_title="rank",
        yaxis_title="share",
        yaxis={"tickformat": ".0%"},
        **DARK,
    )
    return fig


def fig_precision(models):
    fig = make_subplots(rows=1, cols=3, subplot_titles=("top-1 %", "H (bits)", "H / log2 k"))
    xs = [m["model"] for m in models]
    fig.add_trace(go.Scatter(x=xs, y=[top_share(m) for m in models], mode="lines+markers", name="top-1"), row=1, col=1)
    fig.add_trace(go.Scatter(x=xs, y=[entropy(m) for m in models], mode="lines+markers", name="H"), row=1, col=2)
    fig.add_trace(
        go.Scatter(x=xs, y=[normalized_entropy(m) for m in models], mode="lines+markers", name="H/log2k"), row=1, col=3
    )
    fig.update_yaxes(tickformat=".0%", col=1)
    fig.update_layout(title="Precision axis", showlegend=False, **DARK)
    return fig


def prompt_models(experiment: str | None = None):
    """Per (model, prompt) label distributions."""
    with db() as conn:
        query = """
            select run.model, prompt.content as prompt, sample.raw
            from sample
            join run on sample.run_id = run.id
            join prompt on sample.prompt_id = prompt.id
        """
        params = {}
        if experiment is not None:
            query += " where run.experiment = :experiment"
            params["experiment"] = experiment
        rows = conn.execute(query, params).fetchall()

    grouped = {}
    for row in rows:
        model = str(row["model"])
        prompt = str(row["prompt"])
        label = " ".join(str(row["raw"]).split()).lower()
        grouped.setdefault(model, {}).setdefault(prompt, {})
        d = grouped[model][prompt]
        d[label] = d.get(label, 0) + 1

    result = []
    for model, prompts in grouped.items():
        prompt_list = []
        for prompt, dist in prompts.items():
            entries = list(dist.items())
            n = sum(dist.values())
            prompt_list.append({"prompt": prompt, "entries": entries, "n": n})
        result.append({"model": model, "prompts": prompt_list})
    result.sort(key=lambda m: m["model"], reverse=True)
    return result


def fig_prompt_top1(pm):
    prompts = []
    for m in pm:
        for p in m["prompts"]:
            if p["prompt"] not in prompts:
                prompts.append(p["prompt"])
    z = []
    text = []
    avg = []
    for m in pm:
        dmap = {p["prompt"]: p for p in m["prompts"]}
        row = []
        trow = []
        vals = []
        for prompt in prompts:
            p = dmap.get(prompt)
            if p and p["entries"]:
                top = max(c for _, c in p["entries"])
                n = p["n"] or 1
                v = top / n
            else:
                v = 0.0
            row.append(v)
            trow.append(f"{v:.0%}")
            vals.append(v)
        z.append(row)
        text.append(trow)
        avg.append(sum(vals) / len(vals) if vals else 0.0)
    if not z:
        return None
    fig = make_subplots(rows=1, cols=2, column_widths=[0.78, 0.22], subplot_titles=("per prompt", "avg"))
    fig.add_trace(
        go.Heatmap(
            x=prompts,
            y=[m["model"] for m in pm],
            z=z,
            text=text,
            texttemplate="%{text}",
            textfont={"size": 10, "color": "#000000"},
            colorscale=[[0, "#000000"], [1, "#4c8dff"]],
            zmin=0,
            zmax=1,
            hovertemplate="%{y}<br>%{x}: %{z:.1%}<extra></extra>",
            showscale=False,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Heatmap(
            x=["avg"],
            y=[m["model"] for m in pm],
            z=[[v] for v in avg],
            text=[[f"{v:.0%}"] for v in avg],
            texttemplate="%{text}",
            textfont={"size": 10, "color": "#000000"},
            colorscale=[[0, "#000000"], [1, "#4c8dff"]],
            zmin=0,
            zmax=1,
            hovertemplate="%{y}: %{z:.1%}<extra></extra>",
            showscale=True,
        ),
        row=1,
        col=2,
    )
    fig.update_layout(
        title="Top-1 share (model x prompt)",
        **DARK,
    )
    return fig


def fig_prompt_entropy(pm):
    prompts = []
    for m in pm:
        for p in m["prompts"]:
            if p["prompt"] not in prompts:
                prompts.append(p["prompt"])
    z = []
    text = []
    avg = []
    for m in pm:
        dmap = {p["prompt"]: p for p in m["prompts"]}
        row = []
        trow = []
        vals = []
        for prompt in prompts:
            p = dmap.get(prompt)
            if p and p["entries"]:
                n = p["n"] or 1
                h = 0.0
                for _, c in p["entries"]:
                    share = c / n
                    if share > 0:
                        h -= share * math.log2(share)
                v = h
            else:
                v = 0.0
            row.append(v)
            trow.append(f"{v:.2f}")
            vals.append(v)
        z.append(row)
        text.append(trow)
        avg.append(sum(vals) / len(vals) if vals else 0.0)
    if not z:
        return None
    vmax = max((max(row) for row in z + [[v] for v in avg]), default=1) or 1
    fig = make_subplots(rows=1, cols=2, column_widths=[0.78, 0.22], subplot_titles=("per prompt", "avg"))
    fig.add_trace(
        go.Heatmap(
            x=prompts,
            y=[m["model"] for m in pm],
            z=z,
            text=text,
            texttemplate="%{text}",
            textfont={"size": 10, "color": "#000000"},
            colorscale=[[0, "#000000"], [1, "#4c8dff"]],
            zmin=0,
            zmax=vmax,
            hovertemplate="%{y}<br>%{x}: %{z:.2f} bits<extra></extra>",
            showscale=False,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Heatmap(
            x=["avg"],
            y=[m["model"] for m in pm],
            z=[[v] for v in avg],
            text=[[f"{v:.2f}"] for v in avg],
            texttemplate="%{text}",
            textfont={"size": 10, "color": "#000000"},
            colorscale=[[0, "#000000"], [1, "#4c8dff"]],
            zmin=0,
            zmax=vmax,
            hovertemplate="%{y}: %{z:.2f} bits<extra></extra>",
            showscale=True,
        ),
        row=1,
        col=2,
    )
    fig.update_layout(
        title="Entropy (bits) (model x prompt)",
        **DARK,
    )
    return fig


def fig_prompt_pairwise_distance(pm):
    pairs = []
    if pm and pm[0]["prompts"]:
        ps = [p["prompt"] for p in pm[0]["prompts"]]
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                pairs.append((ps[i], ps[j]))

    z = []
    text = []
    for m in pm:
        dmap = {p["prompt"]: p for p in m["prompts"]}
        row = []
        trow = []
        for a, b in pairs:
            left = {"entries": dmap[a]["entries"], "n": dmap[a]["n"]} if a in dmap else {"entries": [], "n": 1}
            right = {"entries": dmap[b]["entries"], "n": dmap[b]["n"]} if b in dmap else {"entries": [], "n": 1}
            d = js_distance(left, right)
            row.append(d)
            trow.append(f"{d:.2f}")
        z.append(row)
        text.append(trow)

    if not z or not pairs:
        return None
    pair_labels = [f"{a[:14]} vs {b[:14]}" for a, b in pairs]
    fig = go.Figure(
        data=go.Heatmap(
            x=pair_labels,
            y=[m["model"] for m in pm],
            z=z,
            text=text,
            texttemplate="%{text}",
            textfont={"size": 9, "color": "#e6e9ef"},
            colorscale=[[0, "#000000"], [0.1, "#2a1a3a"], [1, "#bc8cff"]],
            zmin=0,
            zmax=1,
            hovertemplate="%{y}<br>%{x}: %{z:.3f}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Prompt-pair JS distance by model",
        xaxis_title="prompt pair",
        yaxis_title="model",
        **DARK,
    )
    return fig


def generate_report(experiment: str | None = None, out_dir: Path | None = None) -> Path:
    init_db()
    data = analysis.get_analysis(experiment=experiment)
    models = plane_models(data)
    pm = prompt_models(experiment)

    if out_dir is None:
        out_dir = config.STATIC_DIR / "reports"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    safe = (experiment or "all").replace("/", "_").replace(" ", "_")
    out_path = out_dir / f"{safe}.html"

    figs = []
    if models:
        figs.append(fig_precision(models))
        figs.append(fig_distribution_stacked(models))
        figs.append(fig_heatmap(data, models))
        if len(models) >= 2:
            figs.append(fig_pairwise(models))
        figs.append(fig_rank(models))

    if pm:
        figs.append(fig_prompt_top1(pm))
        figs.append(fig_prompt_entropy(pm))
        pf = fig_prompt_pairwise_distance(pm)
        if pf is not None:
            figs.append(pf)

    body_parts = []
    for fig in figs:
        body_parts.append(fig.to_html(full_html=False, include_plotlyjs=False, div_id=None))

    title = f"llm-bias report: {experiment}" if experiment else "llm-bias report"
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ margin: 0; padding: 24px; background: #000000; color: #e6e9ef;
  font-family: ui-sans-serif, system-ui, sans-serif; }}
h1 {{ font-size: 18px; margin: 0 0 16px; }}
.section {{ margin-bottom: 32px; }}
</style>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
</head>
<body>
<h1>{html.escape(title)}</h1>
{"".join(f'<div class="section">{p}</div>' for p in body_parts) if body_parts else "<p>No data yet.</p>"}
</body>
</html>
"""
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="llm-bias report")
    parser.add_argument("experiment", nargs="?", default=None, help="experiment name (default: all)")
    parser.add_argument("--out-dir", default=None, help="output directory (default: static/reports)")
    args = parser.parse_args(argv)
    path = generate_report(experiment=args.experiment, out_dir=args.out_dir)
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

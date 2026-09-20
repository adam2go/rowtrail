"""Render the recorded exploration medians; does not run a benchmark.

Optional documentation dependency: Matplotlib. No product dependencies change.
Run from any directory with `python3 benchmarks/plot.py`.
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


directory = Path(__file__).resolve().parent / "performance"
summary = json.loads((directory / "summary.json").read_text())
records = {item["report"]: item for item in summary["records"]}
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "svg.fonttype": "none",
    "svg.hashsalt": "rowtrail-alpha2-exploration",
    "text.color": "#123C35",
    "axes.labelcolor": "#123C35",
    "xtick.color": "#53635E",
    "ytick.color": "#123C35",
})

labels = ["RowTrail alpha.1 · CLI", "RowTrail alpha.2 · CLI",
          "RowTrail alpha.2 · NDJSON", "DuckDB 1.5.5 · session",
          "DataFusion 55.0.0 · session"]
colors = ["#BD651C", "#147D70", "#147D70", "#63726E", "#63726E"]
fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.8), sharey=True)
fig.set_facecolor("#FBF8F1")
fig.subplots_adjust(left=0.265, right=0.935, top=0.74, bottom=0.27, wspace=0.22)
fig.text(0.04, 0.92, "Five questions. The whole exploration.",
         fontsize=22, weight="bold")
fig.text(0.04, 0.855, "Open → materialize → two saved-result branches → return to source",
         fontsize=11, color="#53635E")

for axis, rows in zip(axes, (16384, 1048576)):
    before = records[f"before-cli-{rows}.json"]
    after = records[f"after-cli-{rows}.json"]
    session = records[f"after-session-{rows}.json"]
    values = [before["median_ms"], after["median_ms"], session["median_ms"],
              after["duckdb_median_ms"], after["datafusion_median_ms"]]
    axis.set_facecolor("#FBF8F1")
    axis.set_xscale("log")
    axis.set_xlim(2, 70000)
    axis.set_ylim(4.55, -0.55)
    axis.set_xticks([10, 100, 1000, 10000])
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    axis.minorticks_off()
    axis.grid(axis="x", color="#DEE5DE", linewidth=0.8)
    axis.set_yticks(range(len(labels)), labels)
    axis.tick_params(axis="both", length=0, pad=9)
    for spine in axis.spines.values():
        spine.set_visible(False)
    for y, (value, color) in enumerate(zip(values, colors)):
        axis.scatter(value, y, s=65, color=color, zorder=3)
        axis.annotate(f"{value:,.2f}", (value, y), xytext=(10, 0),
                      textcoords="offset points", va="center", fontsize=10,
                      color=color, weight="bold")
    axis.set_title(f"{rows:,} rows", loc="left", pad=20, weight="bold", fontsize=14)

fig.text(0.265, 0.16, "Elapsed milliseconds · logarithmic scale · lower is better",
         fontsize=11, weight="bold")
fig.text(0.04, 0.095,
         "Median of 5 local runs · Apple arm64 · warm OS cache · fresh workspace per run",
         fontsize=10, color="#53635E")
fig.text(0.04, 0.05,
         "RowTrail persists results. Engine baselines keep in-memory intermediates. "
         "DataFusion excludes process startup.", fontsize=9.5, color="#53635E")
destination = directory / "exploration.svg"
fig.savefig(destination, facecolor=fig.get_facecolor(), metadata={
    "Date": None,
    "Title": "RowTrail alpha.2 exploration measurements",
    "Description": "Median of five local runs, logarithmic millisecond axis. "
                   "Source: benchmarks/performance/summary.json. "
                   "Direct persistent engines are faster on these workloads.",
})
plt.close(fig)
# Normalize Matplotlib's path-line whitespace for a clean repository diff.
destination.write_text("\n".join(line.rstrip() for line in destination.read_text().splitlines()) + "\n")
print(destination)

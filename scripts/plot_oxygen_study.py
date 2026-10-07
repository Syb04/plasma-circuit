#!/usr/bin/env python3
"""Plot an exported O2 parameter study without interpolating missing cases.

Usage::

    python scripts/plot_oxygen_study.py --input artifacts/study/cases.csv \
        --output artifacts/study/figures

The figures describe an exploratory reduced closure, not model validation.
Failed solutions are never used as physical results.  CSV reading uses only the
standard library; plotting requires NumPy and Matplotlib.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path
import tempfile
from typing import Any

# This must precede the Matplotlib import in read-only home environments.
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "oxygen-study-matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "oxygen-study-cache"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm, Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import MaxNLocator
import numpy as np


FOOTNOTE = "Exploratory reduced closure; not validation. Only exported cases are shown."
INK = "#263749"
BLUE = "#256b9a"
ACCENT = "#b55924"
FAIL = "#b8bec5"
MISSING = "#f4f5f6"
PARAMETERS = (
    ("hL", r"Common edge factor $h_L=h_R$"),
    ("diffusion_scale", "Diffusion scale"),
    ("gamma_o", r"O recombination probability $\gamma_O$"),
    ("gamma_meta", r"Metastable quenching probability $\gamma_{meta}$"),
    ("nu_s", r"Per-target collision frequency $\nu_m$ [$s^{-1}$]"),
)
METRICS = (
    ("te_ev", r"Electron temperature $T_e$ [eV]"),
    ("ne_m3", r"Electron density $n_e$ [$m^{-3}$]"),
    ("alpha", r"Electronegativity $\alpha$"),
)
LOSS_COMPONENTS = (
    ("loss_ionization_w", "Ionization", "#3976a5"),
    ("loss_excitation_w", "Excitation", "#72a3c8"),
    ("loss_elastic_w", "Elastic", "#76a89c"),
    ("loss_electron_wall_w", "Electron wall", "#d9ad65"),
    ("loss_ion_wall_w", "Ion wall", "#b96d53"),
)


def number(row: dict[str, str], key: str) -> float:
    """Blank, malformed and nonfinite values remain unavailable, never zero."""
    try:
        value = float(row.get(key, ""))
    except (TypeError, ValueError):
        return math.nan
    return value if math.isfinite(value) else math.nan


def converged(row: dict[str, str]) -> bool:
    return str(row.get("converged") or "").strip().lower() in {"1", "true", "yes"}


def read_cases(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        required = {"case_id", "family", "converged"}
        if not required.issubset(fields):
            raise ValueError("CSV must contain case_id, family and converged columns")
        return [dict(row) for row in reader]


def style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "axes.labelcolor": INK,
        "axes.edgecolor": "#b1bbc4",
        "text.color": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "grid.color": "#d9dfe5",
        "grid.linewidth": 0.6,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "svg.fonttype": "none",
    })


def no_data(ax: Any, message: str) -> None:
    ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes,
            color="#6d7884", wrap=True)
    ax.set_xticks([])
    ax.set_yticks([])


def baseline_case(rows: list[dict[str, str]]) -> dict[str, str] | None:
    baselines = [row for row in rows if row.get("family") == "baseline"]
    return next((row for row in baselines if converged(row)), baselines[0] if baselines else None)


def parameter_scale(ax: Any, key: str, values: list[float]) -> None:
    positive = sorted(value for value in values if value > 0 and math.isfinite(value))
    finite = [value for value in values if math.isfinite(value)]
    if not positive:
        return
    if key == "nu_s" and any(value <= 0 for value in finite):
        ax.set_xscale("symlog", linthresh=positive[0] / 10)
    elif all(value > 0 for value in finite) and positive[-1] / positive[0] >= 20:
        ax.set_xscale("log")


def sensitivity_figure(rows: list[dict[str, str]]) -> Any:
    fig, axes = plt.subplots(3, len(PARAMETERS), figsize=(18, 10), squeeze=False)
    baseline = baseline_case(rows)
    for column, (key, label) in enumerate(PARAMETERS):
        sweep = [row for row in rows if row.get("family") == key and math.isfinite(number(row, key))]
        cases = sweep + ([baseline] if baseline is not None and math.isfinite(number(baseline, key)) else [])
        cases.sort(key=lambda row: number(row, key))
        failed = [row for row in sweep if not converged(row)]
        success_count = sum(converged(row) for row in sweep)
        for metric_index, (metric, ylabel) in enumerate(METRICS):
            ax = axes[metric_index, column]
            if not cases:
                no_data(ax, "No exported cases")
            else:
                x = [number(row, key) for row in cases]
                y = [number(row, metric) if converged(row) else math.nan for row in cases]
                # Nonpositive density cannot be represented on the density log axis.
                if metric == "ne_m3":
                    y = [value if value > 0 else math.nan for value in y]
                    if any(math.isfinite(value) for value in y):
                        ax.set_yscale("log")
                ax.plot(x, y, color=BLUE, marker="o", markersize=4, linewidth=1.4)
                parameter_scale(ax, key, x)
                if not any(math.isfinite(value) for value in y):
                    ax.text(0.5, 0.55, "No converged finite result", ha="center", va="center",
                            transform=ax.transAxes, color="#6d7884", fontsize=9)
                if baseline is not None and converged(baseline):
                    bx, by = number(baseline, key), number(baseline, metric)
                    if math.isfinite(bx) and math.isfinite(by) and (metric != "ne_m3" or by > 0):
                        ax.plot(bx, by, marker="D", markersize=7, markerfacecolor="white",
                                markeredgecolor=ACCENT, markeredgewidth=1.6, linestyle="none", zorder=5)
                for row in failed:
                    ax.plot(number(row, key), 0.04, "x", color="#707b85", markersize=6,
                            transform=ax.get_xaxis_transform(), clip_on=False)
                ax.grid(True, alpha=0.75)
                ax.margins(x=0.08)
                if metric == "te_ev":
                    ax.ticklabel_format(axis="y", style="plain", useOffset=False)
            if column == 0:
                ax.set_ylabel(ylabel)
            if metric_index == 0:
                ax.set_title(f"{label}\n{success_count}/{len(sweep)} converged sweep cases", pad=9)
            if metric_index == 2:
                ax.set_xlabel(label, labelpad=8)
    fig.suptitle("Oxygen loss closure: one-parameter sensitivity", fontsize=17, x=0.055, ha="left", y=0.985)
    fig.legend(handles=[
        Line2D([], [], color=BLUE, marker="o", label="Converged case"),
        Line2D([], [], color=ACCENT, marker="D", markerfacecolor="white", linestyle="none", label="Baseline"),
        Line2D([], [], color="#707b85", marker="x", linestyle="none", label="Failed case (x-axis marker)"),
    ], loc="upper right", bbox_to_anchor=(0.975, 0.984), ncol=3, frameon=False, fontsize=9)
    fig.text(0.055, 0.015, FOOTNOTE + " Lines connect sampled settings; failed cases break the line.", fontsize=9, color="#65717e")
    fig.subplots_adjust(left=0.07, right=0.985, bottom=0.10, top=0.88, hspace=0.27, wspace=0.36)
    return fig


def power_reference_figure(rows: list[dict[str, str]]) -> Any:
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.2), gridspec_kw={"width_ratios": [1.05, 1]})
    baseline = baseline_case(rows)
    ax = axes[0]
    ax.set_title("Baseline power budget", loc="left", pad=16, fontweight="bold")
    if baseline is None or not converged(baseline):
        no_data(ax, "No converged baseline was exported")
    else:
        bars = [("power_w", "Input power", INK), ("loss_total_w", "Reported total loss", "#617b91")]
        bars += list(LOSS_COMPONENTS)
        power = number(baseline, "power_w")
        values = [number(baseline, key) for key, _, _ in bars]
        finite_values = [abs(value) for value in values if math.isfinite(value)]
        span = max(finite_values, default=1) or 1
        for index, ((_, label, color), value) in enumerate(zip(bars, values)):
            if math.isfinite(value):
                ax.barh(index, value, color=color, height=0.65)
                fraction = f" ({100 * value / power:.1f}% of input)" if index >= 2 and power > 0 else ""
                text_value = f"{value:.4g} W{fraction}"
                ax.annotate(text_value, (value, index), xytext=(5 if value >= 0 else -5, 0),
                            textcoords="offset points", va="center", ha="left" if value >= 0 else "right", fontsize=9)
            else:
                ax.text(0, index, "Unavailable", va="center", color="#6d7884", fontsize=9)
        ax.set_yticks(range(len(bars)), [label for _, label, _ in bars])
        ax.invert_yaxis()
        minimum = min([0] + [value for value in values if math.isfinite(value)])
        maximum = max([0] + [value for value in values if math.isfinite(value)])
        ax.set_xlim(minimum - (0.05 if minimum >= 0 else 0.35) * span, maximum + 0.65 * span)
        ax.set_xlabel("Power [W]")
        ax.grid(axis="x", alpha=0.7)
        ax.set_axisbelow(True)
        residual = number(baseline, "energy_residual_rel")
        detail = f"Case: {baseline.get('case_id', '(unnamed)')}"
        if math.isfinite(residual):
            detail += f" | relative energy residual: {residual:.2g}"
        ax.text(0, -0.16, detail, transform=ax.transAxes, fontsize=9, color="#65717e")
    ax = axes[1]
    ax.set_title(r"$O_2^+$ density deviation from supplied reference", loc="left", pad=16, fontweight="bold")
    compared: list[tuple[dict[str, str], float]] = []
    for row in rows:
        reference, density = number(row, "reference_o2plus_density_m3"), number(row, "n_O2plus_m3")
        if not converged(row) or not (reference > 0 and density >= 0):
            continue
        deviation = number(row, "reference_deviation_rel")
        if not math.isfinite(deviation):
            deviation = density / reference - 1
        if math.isfinite(deviation):
            compared.append((row, deviation * 100))
    if not compared:
        no_data(ax, "No converged cases with a finite density\nand positive reference were exported")
    else:
        x = np.arange(1, len(compared) + 1)
        y = [deviation for _, deviation in compared]
        ax.axhline(0, color="#7c8792", linewidth=1, linestyle="--")
        ax.scatter(x, y, s=22, color=BLUE, alpha=0.75, linewidths=0)
        for index, (row, deviation) in enumerate(compared, 1):
            if row.get("family") == "baseline":
                ax.scatter(index, deviation, s=65, marker="D", color="white", edgecolor=ACCENT, linewidth=1.5, zorder=4)
        if len(compared) <= 12:
            ax.set_xticks(x, [row.get("case_id", str(index)) for index, (row, _) in enumerate(compared, 1)], rotation=45, ha="right", fontsize=8)
        else:
            ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
        ax.set_xlim(0.5, len(compared) + 0.5)
        ax.set_xlabel("Computed cases with a reference (CSV order)")
        ax.set_ylabel(r"$(n_{O_2^+} / n_{reference} - 1)\,\times\,100$ [%]")
        ax.grid(alpha=0.7)
        ax.text(0, -0.16, f"{len(compared)} exported converged rows; a reference deviation is not validation.",
                transform=ax.transAxes, fontsize=9, color="#65717e")
    fig.suptitle("Oxygen loss closure: energy accounting and reference comparison", fontsize=16, x=0.035, ha="left", y=0.99)
    fig.text(0.035, 0.025, FOOTNOTE, fontsize=9, color="#65717e")
    fig.subplots_adjust(left=0.15, right=0.985, bottom=0.22, top=0.85, wspace=0.37)
    return fig


def short_value(value: float | None) -> str:
    return "unspecified" if value is None else f"{value:.3g}"


def finite_key(row: dict[str, str], key: str) -> float | None:
    value = number(row, key)
    return value if math.isfinite(value) else None


def metric_norm(values: list[float], logarithmic: bool = False) -> Any:
    valid = [value for value in values if math.isfinite(value) and (not logarithmic or value > 0)]
    if not valid:
        return Normalize(0, 1)
    low, high = min(valid), max(valid)
    if logarithmic:
        if low == high:
            low, high = low / 1.1, high * 1.1
        return LogNorm(low, high)
    if low == high:
        delta = max(abs(low) * 0.05, 0.05)
        low, high = low - delta, high + delta
    return Normalize(low, high)


def cross_figure(rows: list[dict[str, str]]) -> Any:
    cross = [row for row in rows if row.get("family") == "cross"
             and math.isfinite(number(row, "hL")) and math.isfinite(number(row, "gamma_meta"))]
    if not cross:
        fig, ax = plt.subplots(figsize=(10, 4.5))
        no_data(ax, "No cross-study cases with finite hL and gamma_meta were exported")
        fig.suptitle("Oxygen loss closure: interaction grid", fontsize=16, x=0.05, ha="left")
        fig.text(0.05, 0.035, FOOTNOTE, fontsize=9, color="#65717e")
        return fig

    h_values = sorted({number(row, "hL") for row in cross})
    gamma_values = sorted({number(row, "gamma_meta") for row in cross})
    groups: dict[tuple[float | None, float | None], list[dict[str, str]]] = {}
    for row in cross:
        groups.setdefault((finite_key(row, "diffusion_scale"), finite_key(row, "nu_s")), []).append(row)
    group_keys = sorted(groups, key=lambda pair: tuple(math.inf if item is None else item for item in pair))
    shape = (len(gamma_values), len(h_values))
    grids: list[tuple[Any, Any, Any, Any]] = []
    te_values: list[float] = []
    ne_values: list[float] = []
    for group_key in group_keys:
        cells: dict[tuple[int, int], list[dict[str, str]]] = {}
        for row in groups[group_key]:
            cell = (gamma_values.index(number(row, "gamma_meta")), h_values.index(number(row, "hL")))
            cells.setdefault(cell, []).append(row)
        te, ne = np.full(shape, np.nan), np.full(shape, np.nan)
        successes, attempts = np.zeros(shape, dtype=int), np.zeros(shape, dtype=int)
        for cell, cases in cells.items():
            successful = [row for row in cases if converged(row)]
            attempts[cell], successes[cell] = len(cases), len(successful)
            for key, grid, aggregate, positive_only in [("te_ev", te, te_values, False), ("ne_m3", ne, ne_values, True)]:
                values = [number(row, key) for row in successful]
                values = [value for value in values if math.isfinite(value) and (not positive_only or value > 0)]
                if values:
                    # Normally there is one case per cell. Repeats are explicitly
                    # summarized by a median, rather than silently overwritten.
                    grid[cell] = float(np.median(values))
                    aggregate.append(grid[cell])
        grids.append((te, ne, successes, attempts))

    fig, axes = plt.subplots(len(group_keys), 3, figsize=(15, 2.9 * len(group_keys) + 1.5), squeeze=False)
    norms = [metric_norm(te_values), metric_norm(ne_values, logarithmic=True), Normalize(0, max(1, max(int(grid[2].max()) for grid in grids)))]
    titles = [r"Median $T_e$ [eV]", r"Median $n_e$ [$m^{-3}$]", "Converged / attempted cases"]
    image_handles: list[Any] = []
    annotate = len(h_values) * len(gamma_values) <= 64
    for index, (group_key, (te, ne, successes, attempts)) in enumerate(zip(group_keys, grids)):
        for metric_index, data in enumerate((te, ne, successes.astype(float))):
            ax = axes[index, metric_index]
            valid = np.isfinite(data) & (successes > 0)
            masked = np.ma.masked_where(~valid, data)
            cmap = matplotlib.colormaps["viridis" if metric_index != 2 else "Blues"].copy()
            cmap.set_bad(FAIL)
            handle = ax.imshow(masked, origin="lower", aspect="auto", cmap=cmap, norm=norms[metric_index], interpolation="nearest")
            if index == 0:
                image_handles.append(handle)
                ax.set_title(titles[metric_index], fontweight="bold", pad=10)
            for (gy, hx), attempted in np.ndenumerate(attempts):
                if not attempted:
                    ax.add_patch(Rectangle((hx - 0.5, gy - 0.5), 1, 1, facecolor=MISSING,
                                           edgecolor="#d5dae0", hatch="///", linewidth=0.5))
                if annotate:
                    if metric_index == 2 and attempted:
                        label = f"{successes[gy, hx]}/{attempted}"
                    elif not attempted:
                        label = "--"
                    elif not valid[gy, hx]:
                        label = "failed" if successes[gy, hx] == 0 else "n/a"
                    elif metric_index == 0:
                        label = f"{data[gy, hx]:.2g}"
                    else:
                        label = f"{data[gy, hx]:.1e}"
                    normalized = norms[metric_index](data[gy, hx]) if valid[gy, hx] else 1
                    color = "white" if valid[gy, hx] and (normalized < 0.55 if metric_index != 2 else normalized > 0.55) else INK
                    ax.text(hx, gy, label, ha="center", va="center", fontsize=8.5, color=color)
            ax.set_xticks(range(len(h_values)), [short_value(value) for value in h_values], rotation=35 if len(h_values) > 5 else 0, ha="right" if len(h_values) > 5 else "center")
            ax.set_yticks(range(len(gamma_values)), [short_value(value) for value in gamma_values])
            ax.set_xlabel(r"Axial edge factor $h_L$ (sampled settings)")
            if metric_index == 0:
                diffusion, nu = group_key
                ax.set_ylabel(r"$\gamma_{meta}$" + f"\nDiffusion scale = {short_value(diffusion)}\n" + r"$\nu_s$" + f" = {short_value(nu)} " + r"$s^{-1}$ per target", labelpad=12)
    fig.suptitle("Oxygen loss closure: interaction grid", fontsize=17, x=0.04, ha="left", y=0.995)
    height = fig.get_figheight()
    fig.subplots_adjust(left=0.13, right=0.92, top=1 - 0.65 / height, bottom=1.75 / height, hspace=0.58, wspace=0.28)
    for metric_index, handle in enumerate(image_handles):
        position = axes[-1, metric_index].get_position()
        # Dedicated axes keep shared colorbars clear of the bottom-row labels.
        colorbar_axis = fig.add_axes([position.x0, 0.85 / height, position.width, 0.12 / height])
        colorbar = fig.colorbar(handle, cax=colorbar_axis, orientation="horizontal")
        colorbar.ax.tick_params(labelsize=8)
        if metric_index == 2:
            colorbar.locator = MaxNLocator(integer=True, nbins=5)
            colorbar.update_ticks()
    fig.legend(handles=[Patch(facecolor=FAIL, label="Failed or unavailable result"), Patch(facecolor=MISSING, edgecolor="#d5dae0", hatch="///", label="No exported case")],
               loc="lower right", bbox_to_anchor=(0.925, 0.43 / height), ncol=2, frameon=False, fontsize=9)
    fig.text(0.04, 0.35 / height, FOOTNOTE, fontsize=9, color="#65717e")
    fig.text(0.04, 0.17 / height, "Median of converged cases per cell; discrete sampled settings, no interpolation. Grey cells have no usable result.", fontsize=8.5, color="#65717e")
    return fig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, type=Path, help="Study cases CSV")
    parser.add_argument("--output", required=True, type=Path, help="Directory for PNG and SVG figures")
    args = parser.parse_args()
    try:
        rows = read_cases(args.input)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.mkdir(parents=True, exist_ok=True)
    style()
    figures = (
        ("01_parameter_sensitivity", sensitivity_figure),
        ("02_power_budget_reference", power_reference_figure),
        ("03_cross_interactions", cross_figure),
    )
    for name, factory in figures:
        figure = factory(rows)
        for extension in ("png", "svg"):
            destination = args.output / f"{name}.{extension}"
            figure.savefig(destination, dpi=180, bbox_inches="tight")
            print(destination)
        plt.close(figure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

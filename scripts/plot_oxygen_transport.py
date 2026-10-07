#!/usr/bin/env python3
"""Compare paired explicit-h and reduced Gudmundsson (2000) transport cases.

Example:
    python scripts/plot_oxygen_transport.py --input reports/study/cases.csv \
        --output reports/study

This is an exploratory reduced closure, not a full 2001 replication or validation.
Failed solutions are excluded from numerical plots. Transport-domain flags are
displayed separately from solver convergence; no missing reference is inferred.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path
import tempfile
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "oxygen-study-matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "oxygen-study-cache"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import ScalarFormatter


INK = "#263749"
GREY = "#79838d"
MODES = (
    ("explicit_h", "Explicit h = 0.2", "#256b9a", "D"),
    ("gudmundsson_2000", "Reduced 2000 transport", "#b55924", "o"),
)
COMPONENTS = (
    ("loss_ionization_w", "Ionization", "#3976a5"),
    ("loss_excitation_w", "Excitation", "#72a3c8"),
    ("loss_elastic_w", "Elastic", "#76a89c"),
    ("loss_electron_wall_w", "Electron wall", "#d9ad65"),
    ("loss_ion_wall_w", "Ion wall", "#b96d53"),
)
FOOTNOTE = "Reduced Gudmundsson (2000) closure; not a full 2001 replication or validation."
DOMAIN_NOTE = "Filled: transport-domain flag true. Hollow grey / hatching: false or unspecified. Failed solutions are excluded."


def number(row: dict[str, str], key: str) -> float:
    try:
        value = float(row.get(key, ""))
    except (TypeError, ValueError):
        return math.nan
    return value if math.isfinite(value) else math.nan


def flag(row: dict[str, str], key: str) -> bool:
    return str(row.get(key) or "").strip().lower() in {"1", "true", "yes"}


def mode_key(row: dict[str, str]) -> str:
    return str(row.get("transport_mode") or "").strip().replace("-", "_")


def near(value: float, target: float) -> bool:
    return math.isfinite(value) and math.isclose(value, target, rel_tol=1e-8, abs_tol=1e-8)


def read_cases(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "family", "converged", "pressure_mtorr", "power_w", "transport_mode"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")
        return [dict(row) for row in reader]


def style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10,
        "axes.labelsize": 10, "axes.titlesize": 12,
        "axes.labelcolor": INK, "text.color": INK,
        "axes.edgecolor": "#b1bbc4", "xtick.color": INK, "ytick.color": INK,
        "axes.spines.top": False, "axes.spines.right": False,
        "grid.color": "#d9dfe5", "grid.linewidth": 0.6,
        "figure.facecolor": "white", "axes.facecolor": "white",
        "savefig.facecolor": "white", "svg.fonttype": "none",
    })


def message(ax: Any, text: str) -> None:
    ax.text(0.5, 0.52, text, ha="center", va="center", transform=ax.transAxes,
            color=GREY, fontsize=10, wrap=True)


def reference_deviation(row: dict[str, str]) -> float:
    density = number(row, "n_O2plus_m3")
    reference = number(row, "reference_o2plus_density_m3")
    if not flag(row, "converged") or not (density >= 0 and reference > 0):
        return math.nan
    exported = number(row, "reference_deviation_rel")
    return 100 * (exported if math.isfinite(exported) else density / reference - 1)


def metric_value(row: dict[str, str], key: str) -> float:
    if not flag(row, "converged"):
        return math.nan
    if key == "reference_deviation":
        return reference_deviation(row)
    value = number(row, key)
    return value if key != "ne_m3" or value > 0 else math.nan


def paired_rows(rows: list[dict[str, str]], pressure: float, mode: str) -> list[dict[str, str]]:
    return sorted((row for row in rows if row.get("family") == "paired" and mode_key(row) == mode
                   and near(number(row, "pressure_mtorr"), pressure)
                   and any(near(number(row, "power_w"), power) for power in (100, 500, 1500))),
                  key=lambda row: number(row, "power_w"))


def paired_figure(rows: list[dict[str, str]]) -> Any:
    fig, axes = plt.subplots(3, 2, figsize=(12.8, 9.8), squeeze=False)
    metrics = (
        ("te_ev", r"Electron temperature $T_e$ [eV]"),
        ("ne_m3", r"Electron density $n_e$ [$m^{-3}$]"),
        ("reference_deviation", r"$O_2^+$ density deviation [%]"),
    )
    for column, pressure in enumerate((1, 10)):
        for metric_index, (key, ylabel) in enumerate(metrics):
            ax = axes[metric_index, column]
            available = 0
            attempts = 0
            converged_count = 0
            if key == "ne_m3":
                ax.set_yscale("log")
            elif key == "te_ev":
                ax.yaxis.set_major_formatter(ScalarFormatter(useOffset=False))
            for mode, _, color, marker in MODES:
                selected = paired_rows(rows, pressure, mode)
                attempts += len(selected)
                converged_count += sum(flag(row, "converged") for row in selected)
                # Lines are allowed only for unique exported conditions. Repeated
                # rows remain visible as points, without arbitrary averaging.
                line_values = []
                for power in (100, 500, 1500):
                    matches = [row for row in selected if near(number(row, "power_w"), power)]
                    line_values.append(metric_value(matches[0], key) if len(matches) == 1 else math.nan)
                ax.plot([100, 500, 1500], line_values, color=color, linewidth=1.4, alpha=0.7)
                for row in selected:
                    power = number(row, "power_w")
                    value = metric_value(row, key)
                    if not flag(row, "converged"):
                        ax.plot(power, 0.035, marker="x", color=GREY, linestyle="none",
                                markersize=6, transform=ax.get_xaxis_transform())
                    elif math.isfinite(value):
                        valid = flag(row, "transport_domain_valid")
                        ax.plot(power, value, marker=marker, markersize=7, linestyle="none",
                                markerfacecolor=color if valid else "white",
                                markeredgecolor=color if valid else GREY, markeredgewidth=1.4, zorder=5)
                        available += 1
                    elif key == "reference_deviation":
                        ax.plot(power, 0.035, marker="|", color="#a9b0b7", linestyle="none",
                                markersize=10, transform=ax.get_xaxis_transform())
            if not available:
                message(ax, "No supplied reference for converged rows" if key == "reference_deviation" and converged_count
                        else "No converged finite result" if attempts else "No exported paired rows")
                ax.set_yticks([])
            if key == "reference_deviation" and available:
                ax.axhline(0, linestyle="--", color="#a0a9b2", linewidth=0.9)
            ax.set_xscale("log")
            ax.set_xticks([100, 500, 1500], ["100", "500", "1500"])
            ax.set_xlim(80, 1900)
            ax.minorticks_off()
            ax.grid(alpha=0.8)
            ax.margins(y=0.12)
            if metric_index == 0:
                ax.set_title(f"{pressure:g} mTorr\n{converged_count}/{attempts} exported paired rows converged", loc="left", fontweight="bold", pad=12)
            if column == 0:
                ax.set_ylabel(ylabel)
            if metric_index == 2:
                ax.set_xlabel("Prescribed absorbed plasma power [W]")
    handles = [Line2D([], [], color=color, marker=marker, label=label) for _, label, color, marker in MODES]
    handles.extend([
        Line2D([], [], color=GREY, marker="o", markerfacecolor="white", linestyle="none", label="Outside / unknown transport regime"),
        Line2D([], [], color=GREY, marker="x", linestyle="none", label="Solver failure"),
        Line2D([], [], color="#a9b0b7", marker="|", markersize=9, linestyle="none", label="Reference unavailable"),
    ])
    fig.suptitle("Oxygen transport closure: paired operating conditions", fontsize=17, x=0.07, ha="left", y=0.985)
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.54, 0.949), ncol=3, frameon=False, fontsize=9)
    fig.text(0.07, 0.047, FOOTNOTE, fontsize=9, color=GREY)
    fig.text(0.07, 0.026, DOMAIN_NOTE, fontsize=8.5, color=GREY)
    fig.text(0.07, 0.006, r"Reference deviation = $(n_{O_2^+}/n_{reference}-1)\times100$; no missing reference is estimated. Exported paired rows only.", fontsize=8.5, color=GREY)
    fig.subplots_adjust(left=0.105, right=0.98, bottom=0.12, top=0.845, hspace=0.24, wspace=0.20)
    return fig


def budget_pair(rows: list[dict[str, str]], mode: str) -> dict[str, str] | None:
    matches = [row for row in paired_rows(rows, 10, mode) if near(number(row, "power_w"), 500)]
    # Ambiguous repeated conditions are not silently selected or averaged.
    return matches[0] if len(matches) == 1 else None


def detail_text(row: dict[str, str] | None) -> str:
    if row is None or not flag(row, "converged"):
        return "No unique converged derived-transport row"
    details = []
    mean_free_path = number(row, "lambda_i_m")
    temperature = number(row, "ion_temperature_k")
    cross_section = number(row, "cross_section_scale")
    if math.isfinite(mean_free_path):
        details.append(r"$\lambda_i$" + f" = {mean_free_path * 1000:.3g} mm")
    if math.isfinite(temperature):
        details.append(r"$T_i$" + f" = {temperature:.3g} K")
    if math.isfinite(cross_section):
        details.append(f"Cross-section scale = {cross_section:.3g}")
    details.append("Transport domain: " + ("flag true" if flag(row, "transport_domain_valid") else "outside / unspecified"))
    return "\n".join(details)


def budget_figure(rows: list[dict[str, str]]) -> Any:
    fig, (budget, edges) = plt.subplots(1, 2, figsize=(12.8, 6.5), gridspec_kw={"width_ratios": [1.15, 1]})
    selected = [budget_pair(rows, mode) for mode, _, _, _ in MODES]
    budget.set_title("Plasma power budget", loc="left", fontweight="bold", pad=15)
    plotted_losses: list[float] = []
    inputs: list[float] = []
    for index, (row, (_, label, _, _)) in enumerate(zip(selected, MODES)):
        if row is None or not flag(row, "converged"):
            budget.text(index, 0.04, "No unique\nconverged row", transform=budget.get_xaxis_transform(),
                        ha="center", va="bottom", fontsize=9, color=GREY)
            continue
        power = number(row, "power_w")
        if math.isfinite(power):
            inputs.append(power)
        values = [number(row, key) for key, _, _ in COMPONENTS]
        if not all(math.isfinite(value) and value >= 0 for value in values):
            budget.text(index, 0.04, "Incomplete\nloss components", transform=budget.get_xaxis_transform(),
                        ha="center", va="bottom", fontsize=9, color=GREY)
            continue
        valid = flag(row, "transport_domain_valid")
        total = 0.0
        for value, (_, _, color) in zip(values, COMPONENTS):
            budget.bar(index, value, bottom=total, width=0.56, color=color if valid else "#ecedef",
                       edgecolor="white" if valid else GREY, linewidth=0.8,
                       hatch=None if valid else "///")
            if power > 0 and value / power >= 0.055:
                budget.text(index, total + value / 2, f"{value:.3g} W", ha="center", va="center", fontsize=8.5, color=INK)
            total += value
        plotted_losses.append(total)
        budget.annotate(f"Component sum: {total:.4g} W", (index, total), xytext=(0, 8),
                        textcoords="offset points", ha="center", fontsize=9)
    for value in sorted(set(inputs)):
        budget.axhline(value, color=INK, linestyle="--", linewidth=1)
    if inputs:
        input_labels = ", ".join(f"{value:g}" for value in sorted(set(inputs)))
        budget.text(0.02, 0.97, f"Prescribed input: {input_labels} W (dashed)", transform=budget.transAxes,
                    va="top", fontsize=9, color=INK)
    maximum = max([1] + plotted_losses + inputs)
    budget.set_ylim(0, maximum * 1.17)
    budget.set_xlim(-0.6, 1.6)
    budget.set_xticks([0, 1], [label for _, label, _, _ in MODES])
    budget.set_ylabel("Power loss [W]")
    budget.grid(axis="y", alpha=0.7)
    budget.set_axisbelow(True)
    # The table also identifies every component when an unspecified transport
    # domain requires a grey stack, including the smallest loss channels.
    keys = [key for key, _, _ in COMPONENTS] + ["loss_total_w"]
    table_values = []
    for row in selected:
        table_values.append([f"{number(row, key):.3g}" if row is not None and flag(row, "converged")
                             and math.isfinite(number(row, key)) else "--" for key in keys])
    budget.text(0, -0.145, "Loss components and reported total [W]", transform=budget.transAxes, fontsize=8.5, color=GREY)
    table = budget.table(cellText=table_values, rowLabels=["Explicit h", "Reduced 2000"],
                         colLabels=["Ionization", "Excitation", "Elastic", "e-wall", "ion-wall", "Total"],
                         cellLoc="center", bbox=[0.07, -0.39, 0.93, 0.21])
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    for (table_row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#d9dfe5")
        cell.set_linewidth(0.5)
        if table_row == 0 and 0 <= column < len(COMPONENTS):
            cell.set_facecolor(matplotlib.colors.to_rgba(COMPONENTS[column][2], 0.22))

    edges.set_title("Ion edge-to-bulk factors", loc="left", fontweight="bold", pad=15)
    available = []
    for index, (row, (_, label, color, _)) in enumerate(zip(selected, MODES)):
        if row is None or not flag(row, "converged"):
            continue
        valid = flag(row, "transport_domain_valid")
        for parameter_index, key in enumerate(("hL", "hR")):
            value = number(row, key)
            if not (math.isfinite(value) and value >= 0):
                continue
            position = parameter_index + (index - 0.5) * 0.31
            edges.bar(position, value, width=0.29, facecolor=color if valid else "white",
                      edgecolor=color if valid else GREY, linewidth=1.5)
            edges.annotate(f"{value:.4g}", (position, value), xytext=(0, 5), textcoords="offset points", ha="center", fontsize=9)
            available.append(value)
    if not available:
        message(edges, "No unique converged edge factors")
    edges.set_ylim(0, max([0.01] + available) * 1.3)
    edges.set_xlim(-0.55, 1.55)
    edges.set_xticks([0, 1], [r"Axial $h_L$", r"Radial $h_R$"])
    edges.set_ylabel("Edge-to-bulk factor [dimensionless]")
    edges.yaxis.set_major_formatter(ScalarFormatter(useOffset=False))
    edges.grid(axis="y", alpha=0.7)
    edges.set_axisbelow(True)
    edges.legend(handles=[Patch(facecolor=color if row is not None and flag(row, "transport_domain_valid") else "white",
                                edgecolor=color if row is not None and flag(row, "transport_domain_valid") else GREY,
                                label=label)
                          for row, (_, label, color, _) in zip(selected, MODES)],
                 loc="upper left", bbox_to_anchor=(0, -0.10), frameon=False, fontsize=9)
    edges.text(0, -0.29, detail_text(selected[1]), transform=edges.transAxes, fontsize=8.5, color=GREY, va="top")
    fig.suptitle("Transport closure comparison: 10 mTorr, 500 W", fontsize=17, x=0.055, ha="left", y=0.985)
    fig.text(0.055, 0.035, FOOTNOTE, fontsize=9, color=GREY)
    fig.text(0.055, 0.011, DOMAIN_NOTE, fontsize=8.5, color=GREY)
    fig.subplots_adjust(left=0.075, right=0.985, bottom=0.34, top=0.86, wspace=0.27)
    return fig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, type=Path, help="Transport cases CSV")
    parser.add_argument("--output", required=True, type=Path, help="Directory for PNG and SVG figures")
    args = parser.parse_args()
    try:
        rows = read_cases(args.input)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.mkdir(parents=True, exist_ok=True)
    style()
    for name, factory in (("01_paired_transport_comparison", paired_figure), ("02_transport_power_budget", budget_figure)):
        fig = factory(rows)
        for extension in ("png", "svg"):
            destination = args.output / f"{name}.{extension}"
            fig.savefig(destination, dpi=180, bbox_inches="tight")
            print(destination)
        plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

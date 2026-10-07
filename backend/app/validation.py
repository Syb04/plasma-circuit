"""Numerical refinement checks, separate from comparison with physical data."""
from __future__ import annotations

import copy
import math
from typing import Any, Callable


def measurement_metrics(result: dict[str, Any]) -> dict[str, float]:
    metrics = {k: float(v) for k, v in result.get("summary", {}).items()
               if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    for plane in result.get("rf_diagnostics", {}).get("measurement_planes", []):
        prefix = plane["name"]
        for key in ("mean_power_w", "voltage_rms_v", "current_rms_a", "phase_deg"):
            value = plane.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value):
                metrics[f"{prefix}.{key}"] = float(value)
        for key in ("real", "imag"):
            value = plane.get("impedance_ohm", {}).get(key)
            if isinstance(value, (int, float)) and math.isfinite(value):
                metrics[f"{prefix}.impedance_{key}_ohm"] = float(value)
    return metrics


def compare_metrics(base: dict[str, float], refined: dict[str, float], tolerance: float,
                    selected: list[str] | None = None) -> dict[str, Any]:
    selected = selected or [key for key in base if key in refined and (
        key in {"dc_self_bias_v", "electron_density_m3", "electron_temperature_ev", "electron_heating_w",
                "electrode_absorbed_power_w", "electron_absorbed_power_w", "absorbed_power_w",
                "absorbed_power_nonuniformity", "electrode_voltage_nonuniformity"}
        or key.startswith("electrode."))]
    errors = {}
    for key in selected:
        if key not in base or key not in refined:
            errors[key] = {"available": False, "passed": False}
            continue
        a, b = base[key], refined[key]
        # Near-zero self-bias and reactive Z are judged with explicit absolute floors.
        floor = 1e-3 if key.endswith(("_v", "_ohm")) else 1e-6
        error = abs(b-a) / max(abs(a), abs(b), floor)
        errors[key] = {"available": True, "baseline": a, "refined": b, "absolute_floor": floor,
                       "relative_error": error, "passed": error <= tolerance}
    return {"passed": bool(errors) and all(item["passed"] for item in errors.values()), "metrics": errors}


def run_refinement(document: dict, analysis: dict, base: dict,
                   execute_once: Callable[[dict, dict], dict]) -> dict:
    config = analysis.get("settings", {}).get("numerical_validation") or {}
    if not config.get("enabled"):
        return base
    tolerance = float(config.get("relative_tolerance", .02))
    if not math.isfinite(tolerance) or not 0 < tolerance <= .2:
        raise ValueError("数値検証の相対許容値は0より大きく0.2以下で指定してください")
    settings = analysis.get("settings", {})
    variants: list[tuple[str, dict]] = []
    prescribed = analysis["kind"] == "global" and (
        settings.get("power_mode") == "prescribed_absorbed"
        or settings.get("electron_heating_model") == "prescribed_power")
    if analysis["kind"] in {"ccp", "global"} and not prescribed:
        if config.get("refine_points", True):
            n = int(settings.get("points_per_cycle", 256))
            if n < 512:
                variants.append(("rf_time_step", {"points_per_cycle": min(512, 2*n)}))
        if config.get("refine_cycles", True):
            n = int(settings.get("cycles", 80))
            if n < 120:
                variants.append(("rf_periods", {"cycles": min(120, 2*n)}))
    elif analysis["kind"] == "radial":
        n = int(settings.get("radial_cells", 12))
        if n < 128:
            variants.append(("radial_mesh", {"radial_cells": min(128, 2*n)}))
    elif analysis["kind"] == "global_transient":
        variants.append(("ode_tolerance", {"macro_relative_tolerance": max(1e-9, float(settings.get("macro_relative_tolerance", 1e-6))*.1)}))
    cases = []
    metric_base = measurement_metrics(base)
    for name, updates in variants:
        refined_analysis = copy.deepcopy(analysis)
        refined_analysis["settings"].update(updates)
        refined_analysis["settings"].pop("numerical_validation", None)
        try:
            result = execute_once(document, refined_analysis)
            comparison = compare_metrics(metric_base, measurement_metrics(result), tolerance, config.get("metrics"))
            cases.append({"name": name, "settings_changed": updates, "solver_converged": bool(result.get("converged")),
                          **comparison, "passed": bool(result.get("converged")) and comparison["passed"]})
        except Exception as exc:
            cases.append({"name": name, "settings_changed": updates, "passed": False, "error": str(exc)})
    passed = bool(base.get("converged")) and bool(cases) and all(case["passed"] for case in cases)
    base.setdefault("diagnostics", {})["numerical_validation"] = {
        "passed": passed, "relative_tolerance": tolerance, "cases": cases,
        "baseline_solver_converged": bool(base.get("converged")),
        "complete": bool(cases), "physical_validation": False,
        "note": "基準結果を保持し、刻み・周期・メッシュの変更で主要指標を比較する。" if cases
                else "指定モデル・上限設定では比較できる精細化条件がないため、合格とは判定しない。"}
    base["converged"] = bool(base.get("converged")) and passed
    return base

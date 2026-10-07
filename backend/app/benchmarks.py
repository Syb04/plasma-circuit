"""Explicit metric references and phase-based comparison, independent of solvers."""
from __future__ import annotations

import csv
import io
import json
import math
from typing import Any

import numpy as np

from . import database as db

# Dimension, scale to canonical unit, canonical unit. No inferred unit conversion.
UNITS = {
    "": ("dimensionless", 1., "1"), "1": ("dimensionless", 1., "1"),
    "Pa": ("pressure", 1., "Pa"), "kPa": ("pressure", 1e3, "Pa"),
    "mTorr": ("pressure", .1333223684, "Pa"), "Torr": ("pressure", 133.3223684, "Pa"),
    "Hz": ("frequency", 1., "Hz"), "kHz": ("frequency", 1e3, "Hz"), "MHz": ("frequency", 1e6, "Hz"),
    "V": ("voltage", 1., "V"), "mV": ("voltage", 1e-3, "V"), "kV": ("voltage", 1e3, "V"),
    "A": ("current", 1., "A"), "mA": ("current", 1e-3, "A"),
    "C": ("charge", 1., "C"),
    "W": ("power", 1., "W"), "mW": ("power", 1e-3, "W"), "kW": ("power", 1e3, "W"),
    "m^-3": ("density", 1., "m^-3"), "m-3": ("density", 1., "m^-3"), "m⁻³": ("density", 1., "m^-3"),
    "cm^-3": ("density", 1e6, "m^-3"), "cm-3": ("density", 1e6, "m^-3"), "cm⁻³": ("density", 1e6, "m^-3"),
    "eV": ("energy", 1., "eV"), "J": ("energy", 1 / 1.602176634e-19, "eV"),
    "K": ("temperature", 1., "K"), "m": ("length", 1., "m"), "cm": ("length", .01, "m"), "mm": ("length", .001, "m"),
    "s": ("time", 1., "s"), "ms": ("time", 1e-3, "s"), "us": ("time", 1e-6, "s"), "ns": ("time", 1e-9, "s"),
}
SUFFIX_UNITS = {"_m3": "m^-3", "_ev": "eV", "_v": "V", "_w": "W", "_pa": "Pa", "_hz": "Hz", "_k": "K", "_a": "A", "_m": "m", "_s": "s"}


def finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def parse_reference(payload: dict) -> dict:
    result = db.json_copy(payload)
    provenance = payload.get("provenance")
    if not provenance or (isinstance(provenance, str) and not provenance.strip()):
        raise ValueError("Reference provenance is required")
    data = payload["data"]
    if len(json.dumps(data).encode()) > 1048576:
        raise ValueError("Reference data exceeds the 1 MiB limit")
    if payload.get("format") == "csv":
        if not isinstance(data, str):
            raise ValueError("CSV data must be supplied as text")
        reader = csv.DictReader(io.StringIO(data.lstrip("\ufeff")))
        if not {"metric", "value", "unit"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV requires metric,value,unit columns")
        rows = list(reader)
    else:
        if isinstance(data, str):
            data = json.loads(data)
        rows = data.get("metrics", []) if isinstance(data, dict) else data
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ValueError("References require 1 to 200 metrics")
    metrics, names = [], set()
    for row in rows:
        name = str(row.get("metric", "")).strip()
        unit = str(row.get("unit", "")).strip()
        if not name or len(name) > 200 or name in names:
            raise ValueError("Metric names must be nonempty and distinct")
        if unit not in UNITS:
            raise ValueError(f"Unsupported reference unit: {unit}")
        value = finite(row["value"], name)
        uncertainty = row.get("uncertainty")
        if uncertainty in (None, ""):
            uncertainty = payload.get("uncertainty")
        if uncertainty is not None:
            uncertainty = finite(uncertainty, "uncertainty")
            if uncertainty < 0:
                raise ValueError("Uncertainty must be nonnegative")
        dimension, scale, canonical = UNITS[unit]
        canonical_value = finite(value * scale, f"{name} after unit conversion")
        canonical_uncertainty = None if uncertainty is None else finite(uncertainty * scale, "uncertainty after unit conversion")
        metrics.append({"metric": name, "value": canonical_value, "unit": canonical,
                        "uncertainty": canonical_uncertainty,
                        "dimension": dimension, "original_value": value, "original_unit": unit})
        names.add(name)
    result.pop("data", None)
    result["metrics"] = metrics
    return result


def summary_metrics(result: dict) -> dict[str, tuple[float, str]]:
    metrics = {}
    explicit_units = {signal["name"]: signal["unit"] for signal in result.get("signals", [])
                      if isinstance(signal, dict) and "name" in signal and "unit" in signal}
    for table in result.get("tables", []):
        if isinstance(table, dict):
            for row in table.get("rows", []):
                if isinstance(row, dict) and "signal" in row and "unit" in row:
                    explicit_units[row["signal"]] = row["unit"]
    explicit_units.update(result.get("summary_units", {}))
    for name, item in result.get("summary", {}).items():
        unit = explicit_units.get(name)
        if isinstance(item, dict):
            unit, item = item.get("unit"), item.get("value")
        if isinstance(item, (float, int)) and not isinstance(item, bool):
            if unit is None:
                unit = next((u for suffix, u in SUFFIX_UNITS.items() if name.lower().endswith(suffix)), "1")
            if unit in UNITS:
                dimension, scale, canonical = UNITS[unit]
                metrics[name] = (finite(finite(item, name) * scale, f"{name} after unit conversion"), dimension)
    return metrics


def run_settings(result: dict, analysis: dict | None = None) -> dict:
    # Resolved inputs include defaults and authored PLASMA/source settings that
    # are absent from the analysis request. The result records what was solved.
    return {**(analysis or {}).get("settings", {}),
            **result.get("model_metadata", {}).get("input_settings", {})}


def rf_frequency(result: dict, analysis: dict) -> Any:
    settings = run_settings(result, analysis)
    return (result.get("model_metadata", {}).get("rf_drive", {}).get("fundamental_frequency_hz")
            or result.get("rf_diagnostics", {}).get("frequency_hz")
            or result.get("diagnostics", {}).get("fundamental_frequency_hz")
            or settings.get("fundamental_frequency_hz") or settings.get("frequency_hz"))


def compare_reference(result: dict, reference: dict, analysis: dict | None = None) -> dict:
    observed = summary_metrics(result)
    comparisons, missing = [], []
    for metric in reference["metrics"]:
        name = metric["metric"]
        if name not in observed or observed[name][1] != metric["dimension"]:
            missing.append(name)
            continue
        value, _ = observed[name]
        difference = value - metric["value"]
        comparisons.append({"metric": name, "reference_value": metric["value"], "run_value": value,
                            "unit": metric["unit"], "absolute_error": abs(difference), "signed_error": difference,
                            "relative_error": None if metric["value"] == 0 else abs(difference / metric["value"]),
                            "uncertainty": metric["uncertainty"],
                            "within_uncertainty": None if metric["uncertainty"] is None else abs(difference) <= metric["uncertainty"]})
    warnings = ["Reference agreement is metric-specific; numerical convergence does not establish physical validation"]
    settings = run_settings(result, analysis)
    for key in ("frequency_hz", "pressure_pa"):
        if key in settings and not math.isclose(float(settings[key]), reference[key], rel_tol=1e-6):
            warnings.append(f"Reference {key} differs from the run input")
    material = settings.get("gas")
    if material is not None and str(material).lower() != str(reference["material"]).lower():
        warnings.append("Reference material differs from the run input")
    return {"validation_status": "reference_comparison", "comparisons": comparisons, "missing_metrics": missing, "warnings": warnings}


def flatten(value: Any, prefix: str = "") -> dict:
    if isinstance(value, dict):
        return {p: v for key, item in value.items() for p, v in flatten(item, f"{prefix}.{key}" if prefix else str(key)).items()}
    if isinstance(value, list):
        if all(isinstance(item, dict) and "id" in item for item in value):
            return {p: v for item in value for p, v in flatten(item, f"{prefix}.{item['id']}").items()}
        return {p: v for index, item in enumerate(value) for p, v in flatten(item, f"{prefix}.{index}").items()}
    return {prefix: value}


def compare_runs(records: list[dict], phase_align: bool = True) -> dict:
    inputs = {r["id"]: flatten({"document": r["snapshot"], "analysis": r["analysis"]}) for r in records}
    paths = sorted({path for value in inputs.values() for path in value})
    differences = [{"path": path, "values": {key: value.get(path) for key, value in inputs.items()}}
                   for path in paths if len({json.dumps(value.get(path), sort_keys=True) for value in inputs.values()}) > 1]
    waveforms = [{"run_id": r["id"], "axis": (r.get("result") or {}).get("axis"), "signals": (r.get("result") or {}).get("signals", [])} for r in records]
    alignment = {"aligned": False, "reason": "Phase alignment was not requested"}
    if phase_align:
        aligned = []
        frequencies = []
        reason = None
        for r in records:
            result = r.get("result") or {}
            axis = result.get("axis") or {}
            try:
                if result.get("kind", r["analysis"].get("kind")) in {"global_transient", "radial"}:
                    raise ValueError("Macro and radial histories are not RF waveforms")
                frequency = finite(rf_frequency(result, r["analysis"]), "frequency_hz")
                time = np.asarray(axis.get("values", []), dtype=float)
                if frequency <= 0 or axis.get("unit") != "s" or time.size < 4 or not np.isfinite(time).all() or np.any(np.diff(time) <= 0):
                    raise ValueError("Time samples and RF frequency are required")
                # Source RF phase is frequency*time, anchored to zero, independent
                # of wall-clock timestamps or solver startup offsets.
                end_cycle = math.floor(time[-1] * frequency + 1e-9)
                start, end = (end_cycle - 1) / frequency, end_cycle / frequency
                if start < time[0] - 1e-12 or end_cycle < 1:
                    raise ValueError("A complete RF cycle is required")
                phase = np.linspace(0, 1, 257)
                signals = []
                for signal in result.get("signals", []):
                    values = np.asarray(signal.get("values", []), dtype=float)
                    if values.size != time.size or not np.isfinite(values).all():
                        continue
                    signals.append({**signal, "values": np.interp(start + phase / frequency, time, values).tolist()})
                if not signals:
                    raise ValueError("No compatible time-domain signals are present")
                aligned.append({"run_id": r["id"], "axis": {"name": "RF phase", "unit": "cycle", "values": phase.tolist()}, "signals": signals})
                frequencies.append(frequency)
            except (ValueError, TypeError, OverflowError):
                reason = "Each run needs finite monotonic time samples, a positive RF frequency, and a complete RF cycle"
                break
        if reason is None:
            waveforms = aligned
            alignment = {"aligned": True, "reason": "Final complete RF cycles aligned to source phase", "frequencies_hz": frequencies}
        else:
            alignment = {"aligned": False, "reason": reason}
    return {"runs": [{"id": r["id"], "status": r["status"], "summary": (r.get("result") or {}).get("summary", {}), "analysis": r["analysis"]} for r in records],
            "input_differences": differences, "phase_alignment": alignment, "waveforms": waveforms}

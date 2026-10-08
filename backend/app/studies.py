"""Bounded numeric sweeps with immutable per-case inputs and attempt history."""
from __future__ import annotations

import itertools
import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import database as db, worker
from .coax import COAX_NUMBERS

# Paths are data selectors, never expressions. Model definitions, identifiers,
# arbitrary nested objects and solver controls cannot be rewritten by a sweep.
SETTING_NAMES = frozenset({
    "frequency_hz", "rf_peak_voltage", "pressure_pa", "gap_m", "gas_temperature_k",
    "cathode_diameter_m", "area_ratio", "electron_density_m3", "electron_temperature_ev",
    "momentum_collision_frequency_hz", "ion_mass_amu", "electronegativity", "wall_edge_factor",
    "plasma_volume_m3", "wall_loss_area_m2", "cycles", "points_per_cycle", "max_global_iterations",
    "time_step", "stop_time", "start_time", "max_step", "start_frequency", "stop_frequency",
    "points", "start", "stop", "step", "absorbed_power_w", "power_w", "radius_m",
    "radial_cells", "axial_cells", "initial_electron_density_m3", "initial_electron_temperature_ev",
    "duration_s", "output_points", "diffusion_coefficient_m2_s",
})
COMPONENT_PATH = re.compile(r"^document\.components\.([A-Za-z][A-Za-z0-9_]*)\.parameters\.([A-Za-z][A-Za-z0-9_]*)$")
WAVEFORM_PATH = re.compile(r"^document\.components\.([A-Za-z][A-Za-z0-9_]*)\.parameters\.waveform\.([A-Za-z][A-Za-z0-9_]*)$")
WAVEFORM_NUMBERS = {"sin": frozenset({"frequency", "amplitude"}),
                    "rf": frozenset({"frequency_hz", "rf_peak_voltage", "second_frequency_hz", "second_rf_peak_voltage"})}
AUTHORED_RF_SETTINGS = frozenset({"frequency_hz", "rf_peak_voltage", "second_frequency_hz", "second_rf_peak_voltage",
                                  "second_phase_deg", "pulse_frequency_hz", "pulse_duty_cycle", "pulse_off_fraction"})
COMPONENT_NUMBERS = frozenset({"value", "dc", "amplitude", "frequency", "offset", "phase", "delay", "damping", "rise_time", "fall_time", "pulse_width", "period", "initial"})
INTEGER_SETTINGS = frozenset({"cycles", "points_per_cycle", "max_global_iterations", "points", "radial_cells", "axial_cells", "output_points"})


def apply_value(document: dict, analysis: dict, path: str, value: float) -> None:
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("Sweep values must be finite numbers")
    if path.startswith("analysis.settings."):
        name = path.removeprefix("analysis.settings.")
        if (name in AUTHORED_RF_SETTINGS and analysis.get("kind") in {"ccp", "global", "global_transient"}
                and any(str(item.get("kind", "")).upper() == "PLASMA" for item in document.get("components", []))
                and not (analysis.get("kind") == "global_transient" and name.startswith("pulse_"))):
            raise ValueError("Authored PLASMA circuits take RF drive from their source waveform; "
                             "sweep an existing document.components.<source_id>.parameters.waveform.<field> instead")
        if name not in SETTING_NAMES:
            raise ValueError(f"Unsupported sweep path: {path}")
        if name in INTEGER_SETTINGS and not float(value).is_integer():
            raise ValueError(f"{name} requires integer values")
        analysis.setdefault("settings", {})[name] = int(value) if name in INTEGER_SETTINGS else value
        return
    waveform_match = WAVEFORM_PATH.fullmatch(path)
    if waveform_match:
        component = next((item for item in document.get("components", []) if item["id"] == waveform_match[1]), None)
        waveform = component.get("parameters", {}).get("waveform") if component else None
        source_kind = str(component.get("kind", "")).upper() if component else ""
        waveform_kind = str(waveform.get("kind", "")).lower() if isinstance(waveform, dict) else ""
        field = waveform_match[2]
        if (source_kind not in {"V", "I"} or field not in WAVEFORM_NUMBERS.get(waveform_kind, ())
                or (waveform_kind == "rf" and source_kind != "V")):
            raise ValueError(f"Unsupported source waveform sweep path: {path}")
        current = waveform.get(field)
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise ValueError(f"Source waveform parameter must already exist as a number: {path}")
        waveform[field] = value
        return
    match = COMPONENT_PATH.fullmatch(path)
    if not match:
        raise ValueError(f"Unsupported sweep path: {path}")
    component = next((item for item in document.get("components", []) if item["id"] == match[1]), None)
    allowed = COAX_NUMBERS if component and str(component["kind"]).upper() == "COAX" else COMPONENT_NUMBERS
    if match[2] not in allowed:
        raise ValueError(f"Unsupported sweep path: {path}")
    if component is None or match[2] not in component.get("parameters", {}):
        raise ValueError(f"Component parameter does not exist: {path}")
    current = component["parameters"][match[2]]
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        raise ValueError(f"Component parameter is not numeric: {path}")
    if str(component["kind"]).upper() == "COAX" and match[2] == "segments":
        if not float(value).is_integer() or not 1 <= value <= 256:
            raise ValueError("Coax segments require an integer from 1 to 256")
        value = int(value)
    component["parameters"][match[2]] = value


def expand_cases(document: dict, analysis: dict, axes: list[dict]) -> list[tuple[dict, dict, dict]]:
    if not 1 <= len(axes) <= 2 or math.prod(len(axis["values"]) for axis in axes) > 100:
        raise ValueError("Studies support one or two axes and at most 100 cases")
    if any(not axis["values"] for axis in axes) or len({axis["path"] for axis in axes}) != len(axes):
        raise ValueError("Sweep axes must be nonempty and distinct")
    cases = []
    for values in itertools.product(*(axis["values"] for axis in axes)):
        case_document, case_analysis = db.json_copy(document), db.json_copy(analysis)
        coordinates = dict(zip((axis["path"] for axis in axes), values))
        for path, value in coordinates.items():
            apply_value(case_document, case_analysis, path, value)
        cases.append((coordinates, case_document, case_analysis))
    return cases


def new_attempt(session: Session, study: db.Study, case: db.StudyCase, number: int,
                employee_id: str, document: dict, analysis: dict, runtime: dict) -> db.SimulationRun:
    run = db.SimulationRun(circuit_id=study.circuit_id, circuit_revision=study.circuit_revision,
                           employee_id=employee_id, snapshot=document, analysis=analysis,
                           runtime_config=db.json_copy(runtime))
    session.add(run)
    session.flush()
    session.add(db.StudyAttempt(case_id=case.id, number=number, run_id=run.id, employee_id=employee_id))
    return run


def case_records(session: Session, study_id: str) -> list[tuple[db.StudyCase, list[tuple[db.StudyAttempt, db.SimulationRun]]]]:
    cases = session.scalars(select(db.StudyCase).where(db.StudyCase.study_id == study_id).order_by(db.StudyCase.case_index)).all()
    records = session.execute(select(db.StudyAttempt, db.SimulationRun).join(db.SimulationRun, db.StudyAttempt.run_id == db.SimulationRun.id)
                              .join(db.StudyCase, db.StudyAttempt.case_id == db.StudyCase.id)
                              .where(db.StudyCase.study_id == study_id).order_by(db.StudyAttempt.number)).all()
    attempts: dict[str, list] = {}
    for attempt, run in records:
        attempts.setdefault(attempt.case_id, []).append((attempt, run))
    return [(case, attempts.get(case.id, [])) for case in cases]


def response(study: db.Study, session: Session, detail: bool = True) -> dict[str, Any]:
    records = case_records(session, study.id)
    counts = {status: 0 for status in ("queued", "running", "succeeded", "failed", "canceled", "timed_out")}
    cases = []
    for case, attempts in records:
        latest = attempts[-1][1]
        counts[latest.status] = counts.get(latest.status, 0) + 1
        cases.append({"id": case.id, "index": case.case_index, "coordinates": case.coordinates,
                      "status": latest.status, "run_id": latest.id,
                      "attempts": [{"number": a.number, "run_id": r.id, "status": r.status, "error": r.error,
                                    "employee_id": a.employee_id, "created_at": a.created_at.isoformat()} for a, r in attempts]})
    if counts["running"]:
        status = "running"
    elif counts["queued"]:
        status = "queued"
    elif study.cancel_requested:
        status = "canceled"
    elif counts["failed"] or counts["timed_out"] or counts["canceled"]:
        status = "partial_failed"
    else:
        status = "succeeded"
    result = {"id": study.id, "name": study.name, "status": status, "circuit_id": study.circuit_id,
              "circuit_revision": study.circuit_revision, "employee_id": study.employee_id,
              "axes": study.axes, "counts": counts, "case_count": len(cases),
              "cancel_requested": study.cancel_requested, "created_at": study.created_at.isoformat()}
    if detail:
        result.update(snapshot=study.snapshot, analysis=study.analysis, cases=cases)
    return result


def enqueue_cases(run_ids: list[str]) -> None:
    # One queue outage must not abandon the durable cases later in the batch.
    for run_id in run_ids:
        try:
            worker.enqueue_run(run_id)
        except Exception:
            worker._finish(run_id, "failed", "Unable to enqueue this study case; resume the study after the queue recovers")

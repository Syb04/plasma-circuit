"""Unified dispatch for circuits, plasma physics, diagnostics and refinement."""
from __future__ import annotations

import copy
import math
from dataclasses import asdict, replace
from typing import Any

from .rf_analysis import attach_rf_analysis
from .validation import run_refinement


def _signals(result: dict) -> dict[str, list]:
    return {s["name"]: s["values"] for s in result.get("signals", [])}


def _prepare(trial, settings):
    from .electron_transport import resolve_electron_transport
    from .surface_models import resolve_surface_model
    neutral = dict(getattr(trial, "neutral_species_densities_m3", None) or (settings.get("electron_transport") or {}).get("neutral_densities_m3") or {trial.gas: trial.neutral_density})
    for table in (settings.get("electron_transport") or {}).get("cross_sections", []):
        if table.get("target"):
            neutral.setdefault(table["target"], 0.0)
    transport = resolve_electron_transport(settings.get("electron_transport"),
        electron_temperature_ev=trial.electron_temperature_ev, neutral_densities_m3=neutral,
        explicit_nu_s=trial.momentum_collision_frequency_hz)
    nu = transport["momentum_collision_frequency_s"]
    if nu <= 0:
        raise ValueError("現在の抵抗性バルクRF解析には正の運動量衝突頻度が必要です")
    changes = {"momentum_collision_frequency_hz": nu}
    surface = resolve_surface_model(settings.get("surface_parameters"),
        areas_m2=settings.get("surface_areas_m2", {"cathode": trial.cathode_area, "anode": trial.anode_area, "wall": 0}),
        plasma_volume_m3=trial.volume, neutral_temperature_k=trial.gas_temperature_k)
    if surface["configured"]:
        for role in ("cathode", "anode"):
            changes[f"secondary_electron_yield_{role}"] = surface["surfaces"][role]["secondary_electron_yield"]
    fields = trial.__dataclass_fields__
    return replace(trial, **{k: v for k, v in changes.items() if k in fields}), transport, surface


def _heat(trial, result, settings, backreaction=False):
    from .electron_heating import resolve_electron_heating
    signals = _signals(result)
    config = copy.deepcopy(settings.get("electron_heating") or {})
    config["effective_circuit_backreaction"] = backreaction
    summary = result["summary"]
    return resolve_electron_heating(config, time_s=result["axis"]["values"],
        bulk_current_a=signals["I(bulk)"], bulk_resistance_ohm=trial.resistance,
        sheath_voltage_v={"cathode": signals["V(sheath_cathode)"], "anode": signals["V(sheath_anode)"]},
        sheath_areas_m2={"cathode": trial.cathode_area, "anode": trial.anode_area},
        electron_density_m3=config.get("edge_electron_density_m3", trial.electron_density_m3*trial.wall_edge_factor),
        electron_temperature_ev=trial.electron_temperature_ev,
        sheath_ion_density_m3=trial.ion_density*trial.wall_edge_factor,
        electrode_absorbed_power_w=summary["electrode_absorbed_power_w"],
        conductive_sheath_power_w=summary["conductive_sheath_power_w"],
        secondary_electron_acceleration_power_w=summary.get("secondary_electron_acceleration_power_w", 0),
        circuit_sheath_heating_power_w=summary.get("sheath_heating_w", 0) if backreaction else None,
        circuit_sheath_resistor_power_w=summary.get("sheath_heating_w", 0)
            if config.get("mode", "bulk_drude") == "bulk_drude" else 0)


def _rf_hooks(document, settings):
    from .plasma import execute_plasma

    def prepare(trial):
        return _prepare(trial, settings)[0]

    def transform(trial, result):
        _, transport, surfaces = _prepare(trial, settings)
        heating_config = settings.get("electron_heating") or {}
        if not heating_config and not result["summary"].get("secondary_electron_acceleration_power_w", 0):
            result.setdefault("model_metadata", {}).update({"electron_transport": transport, "surface_parameters": surfaces})
            return result
        moving = heating_config.get("mode") == "moving_wall_maxwellian"
        iterations = []
        if moving:
            tolerance = float(heating_config.get("budget_relative_tolerance", .01))
            limit = int(heating_config.get("max_rf_iterations", 12))
            if not 2 <= limit <= 24:
                raise ValueError("シース加熱RF反復上限は2〜24で指定してください")
            for iteration in range(limit):
                estimate = _heat(trial, result, settings)
                desired = estimate["electron_sheath_moving_wall_estimated_w"]
                actual = float(result["summary"].get("sheath_heating_w", 0))
                error = abs(actual-desired)/max(actual, desired, 1e-20)
                iterations.append({"iteration": iteration+1, "estimated_w": desired, "circuit_w": actual,
                                   "relative_error": error, "resistance_ohm": trial.sheath_heating_resistance_ohm})
                if error <= tolerance:
                    break
                rms2 = float(result["summary"]["rf_current_rms_a"])**2
                if rms2 < 1e-24:
                    break
                target = desired/rms2
                if not math.isfinite(target) or target > 1e9:
                    raise ValueError("シース加熱の有効抵抗がモデル範囲を超えました")
                resistance = .25*trial.sheath_heating_resistance_ohm + .75*target
                trial = replace(trial, sheath_heating_resistance_ohm=resistance)
                current_settings = {**settings, **asdict(trial)}
                result = execute_plasma(document, {"kind": "ccp", "settings": current_settings})
        report = _heat(trial, result, settings, backreaction=moving)
        result["summary"].update({"electron_heating_w": report["electron_heating_w"],
            "electron_bulk_w": report["electron_bulk_w"], "electron_sheath_heating_w":
                report["electron_sheath_moving_wall_w"]+report["electron_sheath_configured_resistor_w"],
            "electron_secondary_w": report["electron_secondary_w"]})
        metadata = result.setdefault("model_metadata", {})
        metadata.update({"electron_transport": transport, "surface_parameters": surfaces,
                         "electron_heating": {k: v for k, v in report.items() if k not in {"signals", "sheaths"}}})
        metadata["electron_heating_definition"] = "mean(Rbulk*Ibulk**2) + measured circuit sheath-resistor dissipation + secondary acceleration; the moving-wall equivalent resistance is counted once, and sheath capacitor work is excluded"
        if moving:
            metadata["electron_heating"]["effective_resistance_assumption"] = "周期平均の不可逆シース加熱を等価直列抵抗へ戻し、実ngspice電流との固定点を求める。位相依存の運動論を置き換える縮約近似。"
        result.setdefault("diagnostics", {})["electron_heating"] = {"budget": report["budget"],
            "budget_valid": report["budget_valid"], "domain_valid": report["domain_valid"],
            "warnings": report["domain_warnings"], "rf_iterations": iterations,
            "sheaths": {role: {k: v for k, v in data.items() if not isinstance(v, list)}
                        for role, data in report["sheaths"].items()},
            "edge_electron_density_m3": heating_config.get("edge_electron_density_m3", trial.electron_density_m3*trial.wall_edge_factor)}
        if moving:
            result.setdefault("signals", []).append({"name": "P(sheath_moving_wall_estimate)", "unit": "W",
                "values": next(s["values"] for s in report["signals"] if s["name"] == "P(electron_sheath_moving_wall)")})
        if report["budget_valid"] is False or (moving and not report["domain_valid"]):
            result["converged"] = False
        return result
    return prepare, transform


def _postprocess(result: dict, analysis: dict) -> dict:
    settings = analysis.get("settings", {})
    attach_rf_analysis(result, settings)
    if result.get("kind") in {"ccp", "global"} and result.get("axis", {}).get("unit") == "s":
        from .radial_model import electromagnetics_validity
        inputs = result.get("model_metadata", {}).get("input_settings") or settings
        result.setdefault("diagnostics", {})["electromagnetics_validity"] = electromagnetics_validity(inputs)
        config = settings.get("iedf") or {}
        if config.get("enabled"):
            from .ion_transport import solve_iedf
            signals = _signals(result)
            if "V(sheath_cathode)" not in signals:
                raise ValueError("IEDFには駆動電極のシース電圧波形が必要です")
            density = float(result["summary"].get("electron_density_m3", inputs.get("electron_density_m3", 1e16)))
            ion_density = density*(1+float(inputs.get("electronegativity", 0)))*float(inputs.get("wall_edge_factor", .5))
            parameters = {**inputs, **config, "ion_density_m3": ion_density}
            # Continue the complete solved drive period, including beat and
            # pulse frequencies, rather than repeating the last carrier cycle.
            drive = result.get("model_metadata", {}).get("rf_drive", {})
            frequency = drive.get("fundamental_frequency_hz") or result.get("diagnostics", {}).get("fundamental_frequency_hz")
            if frequency:
                parameters["frequency_hz"] = frequency
            ions = config.get("species") or [{"name": "effective_positive_ion", "mass_amu": inputs.get("ion_mass_amu", 39.948),
                                               "density_m3": ion_density, "charge_number": 1}]
            iedf = solve_iedf({"time_s": result["axis"]["values"], "voltage_v": signals["V(sheath_cathode)"]}, parameters, ions)
            result["iedf"] = iedf
            result.setdefault("tables", []).extend(iedf.get("tables", []))
            result.setdefault("diagnostics", {})["iedf_converged"] = bool(iedf.get("converged"))
            result["converged"] = bool(result.get("converged")) and bool(iedf.get("converged"))
    return result


def _execute_once(document: dict, analysis: dict) -> dict:
    from .plasma import execute_plasma
    settings = copy.deepcopy(analysis.get("settings", {}))
    if analysis["kind"] == "radial":
        from .radial_model import solve_radial
        return solve_radial(settings, settings.get("operating_point"))
    if analysis["kind"] == "global_transient":
        from .global_dynamics import execute_global_transient
        # The macro envelope scales cycle-averaged power in global_dynamics.
        # Strip that same envelope from RF sources before solving the carrier,
        # including author-defined external schematics, to avoid applying it twice.
        carrier_document = copy.deepcopy(document)
        pulsed_source_ids = []
        for component in carrier_document.get("components", []):
            waveform = component.get("parameters", {}).get("waveform", {})
            if str(component.get("kind", "")).upper() == "V" and str(waveform.get("kind", "")).lower() == "rf":
                if waveform.get("pulse_frequency_hz") and waveform.get("pulse_duty_cycle", .5) < 1:
                    pulsed_source_ids.append(component["id"])
                waveform.update(pulse_frequency_hz=None, pulse_duty_cycle=1, pulse_off_fraction=1)
        carrier_settings = {**settings, "pulse_frequency_hz": None, "pulse_duty_cycle": 1, "pulse_off_fraction": 1}
        prepare, transform = _rf_hooks(carrier_document, carrier_settings)
        def solver(trial):
            trial = prepare(trial)
            result = execute_plasma(carrier_document, {"kind": "ccp", "settings": {**carrier_settings, **asdict(trial)}})
            return transform(trial, result)
        result = execute_global_transient(document, {**analysis, "settings": settings}, rf_solver=solver)
        if pulsed_source_ids and settings.get("power_mode") == "rf_coupled":
            result.setdefault("model_metadata", {})["rf_carrier_pulse_transform"] = {
                "source_ids": pulsed_source_ids,
                "envelope_source": settings.get("_macro_envelope_source", "explicit macro analysis power controls"),
                "note": "Carrier source envelopes removed; the macro envelope applies to cycle-averaged power once."}
        return result
    if analysis["kind"] in {"ccp", "global"}:
        if analysis["kind"] == "global" and settings.get("gas") == "O2":
            settings.setdefault("chemistry_model", "oxygen_reduced")
        prepare, transform = _rf_hooks(document, settings)
        result = execute_plasma(document, {**analysis, "settings": settings}, rf_prepare=prepare, rf_transform=transform)
        return _postprocess(result, {**analysis, "settings": settings})
    from .engine import execute_circuit
    return _postprocess(execute_circuit(document, analysis), analysis)


def execute_simulation(document: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    analysis = copy.deepcopy(analysis)
    explicit_settings = copy.deepcopy(analysis.get("settings", {}))
    if analysis["kind"] in {"ccp", "global", "global_transient"}:
        loads = [component for component in document.get("components", [])
                 if str(component.get("kind", "")).upper() == "PLASMA"]
        if len(loads) == 1:
            analysis["settings"] = {**copy.deepcopy(loads[0].get("parameters", {})), **analysis.get("settings", {})}
    if analysis["kind"] == "global_transient" and analysis.get("settings", {}).get("power_mode") == "rf_coupled":
        from .external_rf import common_fundamental_frequency
        from .expressions import parse_si
        from .global_dynamics import PowerPulse
        sources = [component for component in document.get("components", [])
                   if str(component.get("kind", "")).upper() == "V" and component.get("parameters", {}).get("waveform")]
        frequencies, envelopes = [], []
        for source in sources:
            waveform = source["parameters"]["waveform"]
            kind = str(waveform.get("kind", "")).lower()
            if kind == "rf":
                frequencies.append(float(waveform.get("frequency_hz", 40e6)))
                if waveform.get("second_rf_peak_voltage", 0):
                    frequencies.append(float(waveform["second_frequency_hz"]))
                envelope = PowerPulse.parse(waveform)
                if envelope.frequency_hz and envelope.duty_cycle < 1 and envelope.off_fraction < 1:
                    envelopes.append((envelope.frequency_hz, envelope.duty_cycle, envelope.off_fraction))
            elif kind == "sin":
                frequencies.append(parse_si(waveform.get("frequency", 1e6)))
        if frequencies:
            settings = analysis.setdefault("settings", {})
            settings["fundamental_frequency_hz"] = common_fundamental_frequency(frequencies)
            chosen = next((source for source in sources if source["id"] == settings.get("rf_source_id")), sources[0])
            waveform = chosen["parameters"]["waveform"]
            kind = str(waveform.get("kind", "")).lower()
            if kind == "rf":
                for key in ("frequency_hz", "rf_peak_voltage", "second_frequency_hz", "second_rf_peak_voltage", "second_phase_deg"):
                    if key in waveform:
                        settings[key] = waveform[key]
            elif kind == "sin":
                settings.update(frequency_hz=parse_si(waveform.get("frequency", 1e6)),
                                rf_peak_voltage=abs(parse_si(waveform.get("amplitude", 1))))
            if envelopes and "pulse_frequency_hz" not in explicit_settings:
                if len(set(envelopes)) != 1:
                    raise ValueError("異なる電源パルスを単一マクロ電力包絡へ置き換えられません。明示したマクロパルス条件を指定してください")
                frequency, duty, off_amplitude = envelopes[0]
                inherited = {"pulse_frequency_hz": frequency, "pulse_duty_cycle": duty,
                             "pulse_off_fraction": off_amplitude**2}
                settings.update({key: value for key, value in inherited.items() if key not in explicit_settings})
                settings["_macro_envelope_source"] = "authored source voltage envelope; OFF amplitude squared under a quadratic power approximation, without an off-state RF solve"
    result = _execute_once(document, analysis)
    return run_refinement(document, analysis, result, _execute_once)

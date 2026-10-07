import copy
import json
from dataclasses import asdict

import numpy as np
import pytest

from app.presets import get_presets
from app.simulation import _postprocess, execute_simulation
from app.validation import compare_metrics, run_refinement


def test_refinement_has_explicit_absolute_floor_near_zero_bias():
    r = compare_metrics({"dc_self_bias_v": 0}, {"dc_self_bias_v": 1e-6}, .02)
    assert r["passed"]
    assert r["metrics"]["dc_self_bias_v"]["absolute_floor"] == .001
    assert not compare_metrics({"dc_self_bias_v": 100}, {"dc_self_bias_v": 110}, .02)["passed"]


def test_radial_dispatch_has_actual_conservation_and_refinement():
    result = execute_simulation({}, {"kind": "radial", "settings": {
        "radial_cells": 12, "radial_feed_radius_m": .01,
        "rf_peak_voltage": 5, "electron_density_m3": 1e16,
        "numerical_validation": {"enabled": True, "relative_tolerance": .1}}})
    assert result["diagnostics"]["power_balance_relative_error"] < 1e-8
    assert result["diagnostics"]["numerical_validation"]["complete"]
    assert "electron_absorbed_power_w" in result["diagnostics"]["numerical_validation"]["cases"][0]["metrics"]


def test_real_ccp_wrapper_rf_measurements_iedf_and_refinement():
    preset = next(p for p in get_presets()["presets"] if p["id"] == "ccp-ar")
    analysis = copy.deepcopy(preset["analysis"])
    analysis["settings"].update({"cycles": 24, "points_per_cycle": 256,
        "iedf": {"enabled": True, "particles_per_species": 128, "steps_per_rf_period": 128},
        "numerical_validation": {"enabled": True, "refine_cycles": False, "relative_tolerance": .05}})
    result = execute_simulation(preset["document"], analysis)
    assert result["rf_diagnostics"]["measurement_planes"][0]["name"] == "electrode"
    assert result["rf_diagnostics"]["measurement_planes"][0]["mean_power_w"] == pytest.approx(result["summary"]["electrode_absorbed_power_w"], rel=1e-6)
    assert result["iedf"]["summary"]
    assert result["diagnostics"]["numerical_validation"]["complete"]
    assert result["converged"]
    assert result["diagnostics"]["numerical_validation"]["passed"]


def test_real_moving_wall_heating_closes_against_rf_resistance():
    preset = next(p for p in get_presets()["presets"] if p["id"] == "ccp-ar")
    analysis = copy.deepcopy(preset["analysis"])
    analysis["settings"].update({"cycles": 24, "points_per_cycle": 256,
        "electron_heating": {"mode": "moving_wall_maxwellian", "max_rf_iterations": 12,
                             "budget_relative_tolerance": .02}})
    result = execute_simulation(preset["document"], analysis)
    heat = result["diagnostics"]["electron_heating"]
    assert heat["rf_iterations"]
    assert result["summary"]["electron_sheath_heating_w"] > 0
    assert heat["budget"]["rf_backreaction_included"]
    assert heat["budget"]["circuit_vs_estimated_sheath_relative_error"] < .02
    assert heat["budget"]["extended_rf_power_relative_error"] < .02
    assert result["converged"]


def test_prescribed_steady_power_does_not_claim_rf_refinement():
    def unused_solver(*args):
        pytest.fail("RF time steps do not refine a prescribed-power steady chemistry solve")
    result = run_refinement({}, {"kind": "global", "settings": {
        "power_mode": "prescribed_absorbed", "numerical_validation": {"enabled": True}}},
        {"converged": True, "summary": {"electron_density_m3": 1e16}}, unused_solver)
    assert not result["converged"]
    assert not result["diagnostics"]["numerical_validation"]["complete"]
    assert not result["diagnostics"]["numerical_validation"]["passed"]


def test_failed_baseline_cannot_pass_numerical_validation():
    analysis = {"kind": "radial", "settings": {"numerical_validation": {"enabled": True}}}
    result = run_refinement({}, analysis, {"converged": False, "summary": {"electron_absorbed_power_w": 1}},
        lambda *_: {"converged": True, "summary": {"electron_absorbed_power_w": 1}})
    assert not result["diagnostics"]["numerical_validation"]["passed"]


def test_iedf_wrapper_continues_complete_beat_period_and_propagates_unresolved():
    fundamental = 1e6
    t = np.linspace(0, 2/fundamental, 4097)
    v = 100+40*np.sin(2*np.pi*2*fundamental*t)+30*np.sin(2*np.pi*fundamental*t)
    def result():
        return {"kind": "ccp", "converged": True, "summary": {"electron_density_m3": 1e16},
            "axis": {"unit": "s", "values": t.tolist()},
            "signals": [{"name": "V(sheath_cathode)", "values": v.tolist()}],
            "model_metadata": {"rf_drive": {"fundamental_frequency_hz": fundamental}}}
    analysis = {"settings": {"frequency_hz": 2*fundamental, "electron_temperature_ev": 3,
        "iedf": {"enabled": True, "particles_per_species": 32, "steps_per_rf_period": 128}}}
    combined = _postprocess(result(), analysis)
    assert combined["iedf"]["diagnostics"]["waveform"]["mean_accelerating_voltage_v"] == pytest.approx(100)
    assert combined["iedf"]["diagnostics"]["waveform"]["periodic_boundary_relative_mismatch"] < 1e-12
    analysis["settings"]["iedf"]["max_transit_periods"] = 1e-6
    unresolved = _postprocess(result(), analysis)
    assert unresolved["iedf"]["summary"]["unresolved_particles"] == 32
    assert not unresolved["converged"]


def test_macro_dispatch_applies_power_pulse_once_in_external_schematic(monkeypatch):
    from app.external_rf import template_document
    from app.plasma import CCPSettings
    document = template_document(CCPSettings(pulse_frequency_hz=1e5, pulse_duty_cycle=.5, pulse_off_fraction=.5),
        {"source_resistance_ohm": 50})
    original = copy.deepcopy(document)
    calls = []
    def carrier(circuit, analysis, **kwargs):
        calls.append((circuit, analysis))
        return {"converged": True, "summary": {"electron_heating_w": 400,
            "ion_acceleration_power_w": 100, "conductive_sheath_power_w": 80, "electrode_absorbed_power_w": 480}}
    monkeypatch.setattr("app.plasma.execute_plasma", carrier)
    result = execute_simulation(document, {"kind": "global_transient", "settings": {
        "gas": "Ar", "power_mode": "rf_coupled", "stop_time_s": 1e-5, "rf_update_interval_s": 5e-6,
        "pulse_frequency_hz": 1e5, "pulse_duty_cycle": .5, "pulse_off_fraction": .5}})
    assert result["converged"]
    assert result["summary"]["total_absorbed_energy_j"] == pytest.approx(480e-5*.75, rel=1e-8)
    assert all(analysis["settings"]["pulse_frequency_hz"] is None for _, analysis in calls)
    for circuit, _ in calls:
        source = next(c for c in circuit["components"] if c["kind"] == "V")
        assert source["parameters"]["waveform"]["pulse_frequency_hz"] is None
    assert document == original


def test_macro_dispatch_inherits_source_envelope_and_carrier_time_scale(monkeypatch):
    from app.external_rf import template_document
    from app.plasma import CCPSettings
    document = template_document(CCPSettings(frequency_hz=20e6, pulse_frequency_hz=1e5,
        pulse_duty_cycle=.5, pulse_off_fraction=.5), {"source_resistance_ohm": 50})
    def carrier(circuit, analysis, **kwargs):
        return {"converged": True, "summary": {"electron_heating_w": 400,
            "ion_acceleration_power_w": 100, "conductive_sheath_power_w": 80, "electrode_absorbed_power_w": 480}}
    monkeypatch.setattr("app.plasma.execute_plasma", carrier)
    result = execute_simulation(document, {"kind": "global_transient", "settings": {
        "gas": "Ar", "power_mode": "rf_coupled", "stop_time_s": 1e-5, "rf_update_interval_s": 5e-6}})
    assert result["converged"]
    assert result["summary"]["total_absorbed_energy_j"] == pytest.approx(480e-5*.625, rel=1e-8)
    assert result["model_metadata"]["time_scales"]["rf_period_s"] == pytest.approx(1/20e6)
    assert result["diagnostics"]["pulse"]["off_fraction"] == .25
    assert "amplitude squared" in result["model_metadata"]["rf_carrier_pulse_transform"]["envelope_source"]


def test_real_external_ccp_combines_rf_heating_and_iedf():
    from app.external_rf import template_document
    from app.plasma import CCPSettings
    settings = asdict(CCPSettings(rf_peak_voltage=100, cycles=24, points_per_cycle=128))
    settings.update({"electron_heating": {"mode": "moving_wall_maxwellian", "max_rf_iterations": 12,
        "budget_relative_tolerance": .02}, "iedf": {"enabled": True, "particles_per_species": 64,
        "steps_per_rf_period": 128}})
    document = template_document(CCPSettings.parse(settings),
        {"source_resistance_ohm": 50, "dc_block_capacitance_f": 1e-9})
    load = next(component for component in document["components"] if component["kind"] == "PLASMA")
    # Optional physics stored on the authored load must reach every RF hook.
    for key in ("electron_heating", "iedf"):
        load["parameters"][key] = settings.pop(key)
    result = execute_simulation(document, {"kind": "ccp", "settings": settings})
    assert result["converged"]
    assert result["model_metadata"]["arbitrary_schematic_coupling"]
    assert result["diagnostics"]["electron_heating"]["budget"]["circuit_vs_estimated_sheath_relative_error"] < .02
    assert result["iedf"]["converged"]
    source = result["rf_diagnostics"]["measurement_planes"][1]
    assert source["forward_power_w"]-source["reflected_power_w"] == pytest.approx(
        source["mean_power_w"]-source["dc_power_w"], abs=1e-10)
    json.dumps(result, allow_nan=False)


def test_real_oxygen_rf_dispatch_closes_reduced_power_budget():
    document = {"parameters": {"builtin_ccp_template": 1}, "components": [], "wires": []}
    settings = {"gas": "O2", "power_mode": "rf_coupled", "gas_temperature_k": 600,
        "wall_edge_factor": .2, "cathode_diameter_m": .304, "gap_m": .076,
        "diffusion_o_m2_s": 1.2, "diffusion_o2_m2_s": .84,
        "frequency_hz": 13.56e6, "rf_peak_voltage": 500, "pressure_pa": 6.6661184,
        "cycles": 32, "points_per_cycle": 128, "momentum_collision_frequency_hz": 1e10,
        "electron_momentum_nu_s": {"O": 5e9, "O2": 5e9}}
    result = execute_simulation(document, {"kind": "global", "settings": settings})
    assert result["converged"]
    assert result["rf_diagnostics"]["measurement_planes"][0]["mean_power_w"] == pytest.approx(
        result["summary"]["electrode_absorbed_power_w"], rel=1e-6)
    assert result["summary"]["reduced_energy_loss_w"] == pytest.approx(
        result["summary"]["total_plasma_absorbed_power_w"], rel=1e-6)
    assert result["diagnostics"]["oxygen_balances"]["checks"]["oxygen_atoms"]
    assert len(result["diagnostics"]["global_iterations"]) >= 2
    json.dumps(result, allow_nan=False)

"""External RF regression checks against real ngspice, without fitted data."""
from __future__ import annotations

import ctypes.util
import importlib.util
import json
from dataclasses import asdict

import numpy as np
import pytest

from app.engine import build_netlist
from app.external_rf import RFDrive, common_fundamental_frequency, solve_external_ccp, template_document
from app.plasma import CCPSettings, execute_plasma, solve_ccp
from app.presets import component, wire


requires_solver = pytest.mark.skipif(
    importlib.util.find_spec("PySpice") is None or ctypes.util.find_library("ngspice") is None,
    reason="real PySpice/ngspice is required",
)
TEMPLATE = {"parameters": {"builtin_ccp_template": 1}, "components": [], "wires": []}


def test_common_period_validation_and_pulse_expression():
    drive = RFDrive.from_settings({"frequency_hz": 40e6, "second_frequency_hz": 13.333333333333333e6,
                                  "second_rf_peak_voltage": 10, "pulse_frequency_hz": 6.666666666666667e6})
    assert drive.fundamental_frequency_hz == pytest.approx(6.666666666666667e6)
    assert "floor(time*" in drive.expression()
    assert "+10*sin" in drive.expression()
    with pytest.raises(ValueError, match="整数比"):
        RFDrive.from_settings({"frequency_hz": 40e6, "second_frequency_hz": 40e6*np.sqrt(2), "second_rf_peak_voltage": 10})
    with pytest.raises(ValueError, match="整数倍"):
        RFDrive.from_settings({"frequency_hz": 40e6, "second_frequency_hz": 13e6, "second_rf_peak_voltage": 10, "fundamental_frequency_hz": 2e6})
    with pytest.raises(ValueError, match="second_frequency_hz"):
        CCPSettings.parse({"second_rf_peak_voltage": 10})
    assert common_fundamental_frequency([40e6, 20e6, 10e6]) == 10e6
    with pytest.raises(ValueError, match="整数比"):
        common_fundamental_frequency([40e6, 20e6, 40e6*np.sqrt(2)])


def test_plasma_stamp_and_no_silent_external_template_parameters():
    settings = CCPSettings(cycles=16, points_per_cycle=64)
    document = template_document(settings, {"source_resistance_ohm": 50, "dc_block_capacitance_f": 1e-9})
    netlist = build_netlist(document)
    assert "V_pl_plasma_sense" in netlist
    assert "Bpl_plasma_charge_c" in netlist
    assert "C_dc_block" in netlist
    assert "R_source_r" in netlist
    with pytest.raises(ValueError, match="未対応設定"):
        template_document(settings, {"unknown_component": 1})
    with pytest.raises(ValueError, match="source電圧"):
        template_document(settings, {"voltage_definition": "electrode"})


@requires_solver
def test_explicit_dc_feed_preserves_nonzero_current_and_source_definition():
    result = execute_plasma(TEMPLATE, {"kind": "ccp", "settings": {
        "rf_peak_voltage": 100, "cycles": 24, "points_per_cycle": 128,
        "external_circuit": {"source_resistance_ohm": 50}}})
    assert result["converged"]
    assert not result["diagnostics"]["dc_equilibrium_required"]
    assert result["diagnostics"]["dc_root_evaluations"] == 0
    assert abs(result["diagnostics"]["mean_electrode_current_a"]) > 10*result["diagnostics"]["dc_current_tolerance_a"]
    assert result["model_metadata"]["rf_drive"]["voltage_definition"] == "source"
    signals = {s["name"]: np.asarray(s["values"]) for s in result["signals"]}
    assert "V(source_port)" in signals and "I(source_port)" in signals
    assert np.max(np.abs(signals["V(cathode)"])) < 100
    json.dumps(result, allow_nan=False)


@requires_solver
def test_dc_block_matching_and_transmission_line_close_charge_equilibrium():
    settings = CCPSettings(rf_peak_voltage=100, cycles=24, points_per_cycle=128)
    document = template_document(settings, {"source_resistance_ohm": 50, "series_inductance_h": 1e-7,
                                            "dc_block_capacitance_f": 1e-9, "shunt_capacitance_f": 1e-11})
    document["components"].append(component("line", "T", 400, 100, {"impedance": 50, "delay": 1e-9}, ["p1", "n1", "p2", "n2"]))
    electrode = next(w for w in document["wires"] if w["id"] == "electrode")
    electrode["target"] = {"component_id": "line", "port": "p1"}
    document["wires"].extend([wire("line_output", "line", "p2", "plasma", "p"), wire("line_r1", "line", "n1", "gnd", "g"), wire("line_r2", "line", "n2", "gnd", "g")])
    result = solve_external_ccp(settings, document)
    assert result["converged"]
    assert result["diagnostics"]["dc_equilibrium_required"]
    assert result["diagnostics"]["dc_root_evaluations"] > 1
    assert abs(result["diagnostics"]["mean_electrode_current_a"]) < result["diagnostics"]["dc_current_tolerance_a"]
    assert result["diagnostics"]["rf_power_balance_relative_error"] < .01
    assert "T_line" in result["netlist"] and "C_dc_block" in result["netlist"]
    assert result["summary"]["dc_self_bias_v"] < 0


@requires_solver
def test_dual_frequency_pulsed_source_and_real_heating_backreaction():
    settings = CCPSettings(rf_peak_voltage=40, second_frequency_hz=20e6, second_rf_peak_voltage=10,
                           pulse_frequency_hz=10e6, pulse_duty_cycle=.5, pulse_off_fraction=.2,
                           cycles=24, points_per_cycle=128, sheath_heating_resistance_ohm=1)
    document = template_document(settings, {"source_resistance_ohm": 50})
    result = execute_plasma(document, {"kind": "ccp", "settings": asdict(settings)})
    assert result["diagnostics"]["fundamental_frequency_hz"] == 10e6
    assert len(result["axis"]["values"]) > settings.points_per_cycle*2
    assert result["summary"]["sheath_heating_w"] > 0
    assert result["summary"]["electron_heating_w"] == pytest.approx(result["summary"]["electron_bulk_w"]+result["summary"]["sheath_heating_w"])
    assert "R_pl_plasma_heat" in result["netlist"]
    assert result["diagnostics"]["rf_power_balance_relative_error"] < .01


def test_multiple_plasma_loads_are_rejected_before_solver():
    settings = CCPSettings()
    document = template_document(settings, {})
    document["components"].append(component("other", "PLASMA", 660, 120, {}))
    with pytest.raises(ValueError, match="1個"):
        execute_plasma(document, {"kind": "ccp"})


@requires_solver
def test_legacy_heating_resistor_is_dissipative_and_changes_waveform():
    baseline = solve_ccp(CCPSettings(rf_peak_voltage=60, cycles=24, points_per_cycle=128))
    heated = solve_ccp(CCPSettings(rf_peak_voltage=60, cycles=24, points_per_cycle=128, sheath_heating_resistance_ohm=1))
    assert "Rheat bulk bulk_heat 1" in heated["netlist"]
    assert heated["summary"]["sheath_heating_w"] > 0
    assert heated["summary"]["rf_current_rms_a"] != pytest.approx(baseline["summary"]["rf_current_rms_a"], rel=1e-3)
    assert heated["diagnostics"]["rf_power_balance_relative_error"] < .01


@requires_solver
def test_argon_global_uses_external_network_on_every_rf_iteration():
    settings = CCPSettings(rf_peak_voltage=100, cycles=80, points_per_cycle=128)
    document = template_document(settings, {"source_resistance_ohm": 50})
    result = execute_plasma(document, {"kind": "global", "settings": asdict(settings)})
    assert result["converged"]
    assert result["model_metadata"]["arbitrary_schematic_coupling"]
    assert "R_source_r" in result["netlist"] and "V_pl_plasma_sense" in result["netlist"]
    assert result["summary"]["electron_density_m3"] != settings.electron_density_m3
    balances = result["diagnostics"]["global_balances"]
    assert abs(balances["particle_residual_m3_s"])/balances["particle_source_m3_s"] < 1e-8
    assert abs(balances["energy_residual_w"])/balances["electron_energy_loss_w"] < .01
    assert not result["diagnostics"]["dc_equilibrium_required"]

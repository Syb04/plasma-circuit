"""Independent geometry/ABCD checks and real ngspice coax regressions."""
from __future__ import annotations

import cmath
import json
import math

import numpy as np
import pytest
from scipy.special import iv

from app.coax import Coax, DEFAULT_COAX_PARAMETERS, EPS0, MU0
from app.engine import CircuitError, build_netlist, execute_circuit
from app.external_rf import _dc_cluster, solve_external_ccp, template_document
from app.plasma import CCPSettings
from app.studies import expand_cases
from app.presets import component, wire, get_presets
from test_engine import graph, SOLVER_PRESENT
from test_api import client  # noqa: F401 - isolated DB/queue fixture

native = pytest.mark.skipif(not SOLVER_PRESENT, reason="real ngspice required")


def circuit(parameters=None, source=None, resistance=50, source_resistance=50, cid="coax1", kind="COAX"):
    pins = {"p1": "in", "p2": "out"} if kind == "COAX_GND" else {"p1": "in", "n1": "0", "p2": "out", "n2": "shield"}
    return graph([("source", "V", {"p": "src", "n": "0"}, source or {"dc": 1, "ac_magnitude": 1}),
        ("rs", "R", {"p": "src", "n": "in"}, {"value": source_resistance}),
        (cid, kind, pins, parameters or {}),
        ("load", "R", {"p": "out", "n": "0" if kind == "COAX_GND" else "shield"}, {"value": resistance})])


def test_tem_geometry_and_epsilon_mu_scaling():
    baseline = Coax.parse({"inner_resistivity_ohm_m": 0, "shield_resistivity_ohm_m": 0, "loss_tangent": 0})
    expected_l = MU0 / (2 * math.pi) * math.log(3.35)
    expected_c = 2 * math.pi * EPS0 * 2.1 / math.log(3.35)
    assert baseline.inductance_h_m == pytest.approx(expected_l)
    assert baseline.capacitance_f_m == pytest.approx(expected_c)
    assert baseline.metadata()["tem_delay_s"] == pytest.approx(math.sqrt(MU0 * EPS0 * 2.1))
    changed = Coax.parse({**baseline.parameters, "relative_permittivity": 8.4, "relative_permeability": 9, "length_m": 2})
    assert changed.inductance_h_m == pytest.approx(9 * expected_l)
    assert changed.capacitance_f_m == pytest.approx(4 * expected_c)
    assert changed.metadata()["nominal_impedance_ohm"] == pytest.approx(1.5 * baseline.metadata()["nominal_impedance_ohm"])
    assert changed.metadata()["tem_delay_s"] == pytest.approx(12 * baseline.metadata()["tem_delay_s"])


def test_skin_impedance_matches_cylinder_solution_and_surface_limit():
    p = {**DEFAULT_COAX_PARAMETERS, "reference_frequency_hz": 1e9}
    cable = Coax.parse(p)
    a, b = p["inner_diameter_m"] / 2, p["shield_inner_diameter_m"] / 2
    k = cmath.sqrt(1j * 2 * math.pi * 1e9 * MU0 / p["inner_resistivity_ohm_m"])
    expected = p["inner_resistivity_ohm_m"] * k / (2 * math.pi) * (iv(0, k*a) / iv(1, k*a) / a + 1 / b)
    assert cable.rf_resistance_ohm_m == pytest.approx(expected.real, rel=1e-6)
    assert cable.internal_reactance_ohm_m == pytest.approx(expected.imag, rel=1e-6)
    assert cable.rf_resistance_ohm_m > 10 * cable.dc_resistance_ohm_m
    omega = 2 * math.pi * p["reference_frequency_hz"]
    fitted = cable.dc_resistance_ohm_m + 1 / (1 / cable.skin_resistance_ohm_m + 1 / (1j * omega * cable.skin_inductance_h_m))
    assert fitted == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("parameters", [
    {"inner_diameter_m": 0}, {"shield_inner_diameter_m": 1e-3}, {"length_m": -1},
    {"relative_permittivity": 0}, {"relative_permeability": -1}, {"loss_tangent": 1},
    {"inner_resistivity_ohm_m": -1}, {"shield_resistivity_ohm_m": True}, {"shield_thickness_m": 0},
    {"reference_frequency_hz": "nan"}, {"segments": 1.5}, {"segments": 257}, {"segments": False},
    {"length_m": "inf"}, {"inner_diameter_m": "1e-308"}, {"unknown": 1},
])
def test_invalid_materials_and_dimensions_fail_before_solver(parameters):
    with pytest.raises(ValueError):
        Coax.parse(parameters)
    with pytest.raises(CircuitError):
        build_netlist(circuit(parameters))


def test_preview_is_read_only_validates_and_accepts_scientific_values(client):
    response = client.post("/api/coax/preview", json={"parameters": {"inner_diameter_m": "1e-3", "reference_frequency_hz": "4e7"}})
    assert response.status_code == 200, response.text
    assert response.json()["parameters"]["reference_frequency_hz"] == 40e6
    assert response.json()["nominal_impedance_ohm"] == pytest.approx(50.021084987)
    assert client.get("/api/circuits").json()["total_all"] == 0
    assert not client.queue.jobs
    invalid = client.post("/api/coax/preview", json={"parameters": {"shield_inner_diameter_m": 1e-3}})
    assert invalid.status_code == 422 and "シールド内径" in invalid.json()["detail"]
    assert client.post("/api/coax/preview", json={"parameters": {}, "force": True}).status_code == 422


@pytest.mark.parametrize("kind", ["COAX", "COAX_GND"])
def test_shield_reference_and_dc_clusters_do_not_short_the_inner_conductor(kind):
    from app.engine import _topology
    doc = circuit(kind=kind)
    _, _, nets = _topology(doc)
    if kind == "COAX":
        assert nets[("coax1", "n1")] == nets[("coax1", "n2")] == "0"
    else:
        assert {port for owner, port in nets if owner == "coax1"} == {"p1", "p2"}
    assert nets[("coax1", "p1")] != "0" and nets[("coax1", "p2")] != "0"
    # Isolate the line to prove that its two conductors are not merged at DC.
    cable_only = {"components": [c for c in doc["components"] if c["kind"] == kind]}
    connected = _dc_cluster(cable_only, nets[("coax1", "p1")], nets)
    assert nets[("coax1", "p2")] in connected and "0" not in connected
    initial = {"coax1.p1": "2e2", "coax1.p2": "2e2"}
    if kind == "COAX":
        initial["coax1.n1"] = 0
    netlist = build_netlist(doc, {"kind": "transient", "settings": {"initial_conditions": initial}})
    assert "IC=200" in next(line for line in netlist.splitlines() if line.startswith("C_cx_coax1_16 "))


def test_total_discretization_is_bounded_and_ports_are_fixed():
    doc = circuit({"segments": 256})
    for i in range(2):
        doc["components"].append({"id": f"extra{i}", "kind": "COAX_GND" if i == 0 else "COAX",
            "ports": ["p1", "p2"] if i == 0 else ["p1", "n1", "p2", "n2"], "parameters": {"segments": 256}})
    with pytest.raises(CircuitError, match="512"):
        build_netlist(doc)
    doc = circuit()
    doc["components"][2]["ports"].append("extra")
    with pytest.raises(CircuitError, match="4端子"):
        build_netlist(doc)
    doc = circuit(kind="COAX_GND")
    doc["components"][2]["ports"].append("n1")
    with pytest.raises(CircuitError, match="2端子"):
        build_netlist(doc)
    doc = circuit(kind="COAX_GND")
    doc["wires"].append(wire("hidden_shield", "coax1", "n1", "gnd", "g"))
    with pytest.raises(CircuitError, match="配線先の端子"):
        build_netlist(doc)


@pytest.mark.parametrize("kind", ["COAX", "COAX_GND"])
def test_coax_study_sweeps_keep_original_inputs_and_require_integer_sections(kind):
    doc = circuit(DEFAULT_COAX_PARAMETERS.copy(), kind=kind)
    cases = expand_cases(doc, {"kind": "ac", "settings": {}}, [{"path": "document.components.coax1.parameters.length_m", "values": [.5, 2]}])
    assert [case[1]["components"][2]["parameters"]["length_m"] for case in cases] == [.5, 2]
    assert doc["components"][2]["parameters"]["length_m"] == 1
    with pytest.raises(ValueError, match="integer"):
        expand_cases(doc, {"kind": "ac"}, [{"path": "document.components.coax1.parameters.segments", "values": [16.5]}])


@native
@pytest.mark.parametrize("kind", ["COAX", "COAX_GND"])
@pytest.mark.parametrize("loss_tangent", [0, .03])
def test_ac_complex_transfer_matches_independent_distributed_abcd_solution(loss_tangent, kind):
    p = {"segments": 64, "inner_resistivity_ohm_m": 0, "shield_resistivity_ohm_m": 0, "loss_tangent": loss_tangent}
    result = execute_circuit(circuit(p, resistance=100, cid="COAX1", kind=kind), {"kind": "ac", "settings": {"start_frequency": 39e6, "stop_frequency": 41e6, "variation": "lin", "points": 3}})
    omega = 2 * math.pi * 40e6
    l = MU0 / (2 * math.pi) * math.log(3.35)
    c = 2 * math.pi * EPS0 * 2.1 / math.log(3.35)
    z, y = 1j * omega * l, omega * c * complex(loss_tangent, 1)
    gamma, z0 = cmath.sqrt(z*y), cmath.sqrt(z/y)
    a, b, cc, d = cmath.cosh(gamma), z0*cmath.sinh(gamma), cmath.sinh(gamma)/z0, cmath.cosh(gamma)
    expected = 1 / (a + b / 100 + 50 * (cc + d / 100))
    net = result["diagnostics"]["node_map"]["COAX1.p2"]
    v = result["complex_vectors"][net]
    actual = complex(v["real"][1], v["imag"][1])
    assert actual == pytest.approx(expected, rel=2e-4)
    assert not any("cx_" in s["name"] for s in result["signals"])
    assert all(len(s["values"]) == len(result["axis"]["values"]) for s in result["signals"])
    assert result["model_metadata"]["coax_cables"]["COAX1"]["attenuation_db"] >= 0
    assert result["model_metadata"]["coax_cables"]["COAX1"]["component_kind"] == kind
    assert result["model_metadata"]["coax_cables"]["COAX1"]["shield_reference_node"] == "0"
    json.dumps(result, allow_nan=False)


@native
@pytest.mark.parametrize("kind", ["COAX", "COAX_GND"])
def test_dc_keeps_true_conductor_resistance_and_no_dielectric_leakage(kind):
    cable = Coax.parse({"loss_tangent": .2})
    result = execute_circuit(circuit({"loss_tangent": .2}, resistance=1e6, source={"dc": 250}, kind=kind), {"kind": "op"})
    expected = 250 / (1e6 + 50 + cable.dc_resistance_ohm_m)
    assert result["summary"]["I(coax1:input)"] == pytest.approx(expected, rel=1e-5)
    assert result["summary"]["V(coax1:output)"] == pytest.approx(expected * 1e6, rel=1e-6)


@native
@pytest.mark.parametrize("kind", ["COAX", "COAX_GND"])
def test_step_delay_and_matching_agree_with_tem_propagation(kind):
    p = {"segments": 64, "inner_resistivity_ohm_m": 0, "shield_resistivity_ohm_m": 0, "loss_tangent": 0}
    z0 = math.sqrt(MU0 / EPS0 / 2.1) / (2 * math.pi) * math.log(3.35)
    source = {"dc": 0, "waveform": {"kind": "pulse", "initial": 0, "pulsed": 1, "delay": 2e-9,
        "rise": 1e-9, "fall": 1e-9, "width": 80e-9, "period": 200e-9}}
    result = execute_circuit(circuit(p, source=source, resistance=z0, source_resistance=z0, kind=kind),
        {"kind": "transient", "settings": {"time_step": .02e-9, "max_step": .02e-9, "stop_time": 30e-9}})
    voltage = next(s["values"] for s in result["signals"] if s["name"] == "V(coax1:output)")
    t = np.asarray(result["axis"]["values"])
    rising = np.flatnonzero(np.asarray(voltage) >= .25)[0]
    crossing = np.interp(.25, voltage[rising-1:rising+1], t[rising-1:rising+1])
    delay = math.sqrt(MU0 * EPS0 * 2.1)
    assert crossing == pytest.approx(2.5e-9 + delay, abs=.12e-9)
    assert voltage[-1] == pytest.approx(.5, abs=2e-3)


@native
@pytest.mark.parametrize("kind", ["COAX", "COAX_GND"])
@pytest.mark.parametrize("dc_block", [False, True])
def test_coax_preset_and_periodic_ccp_include_terminal_power_and_metadata(dc_block, kind):
    preset = next(p for p in get_presets()["presets"] if p["id"] == ("coax_grounded" if kind == "COAX_GND" else "coax"))
    result = execute_circuit(preset["document"], preset["analysis"])
    assert result["converged"] and "coax1" in result["model_metadata"]["coax_cables"]
    settings = CCPSettings(cycles=32, points_per_cycle=128, rf_peak_voltage=100)
    doc = template_document(settings, {"source_resistance_ohm": 50, "dc_block_capacitance_f": 1e-9 if dc_block else 0})
    doc["components"].append(component("coax1", kind, 350, 120, {"length_m": .5, "segments": 16}, ["p1", "p2"] if kind == "COAX_GND" else ["p1", "n1", "p2", "n2"]))
    electrode = next(w for w in doc["wires"] if w["id"] == "electrode")
    electrode["target"] = {"component_id": "coax1", "port": "p1"}
    doc["wires"].append(wire("line_out", "coax1", "p2", "plasma", "p"))
    if kind == "COAX":
        doc["wires"].append(wire("line_return", "coax1", "n1", "gnd", "g"))
    result = solve_external_ccp(settings, doc)
    assert result["converged"]
    assert result["diagnostics"]["dc_equilibrium_required"] == dc_block
    assert result["model_metadata"]["coax_cables"]["coax1"]["periodic_net_input_power_w"] > 0
    assert {"V(coax1:input)", "V(coax1:output)", "I(coax1:input)", "P(coax1) net input"} <= {s["name"] for s in result["signals"]}
    assert all(len(s["values"]) == len(result["axis"]["values"]) for s in result["signals"])
    json.dumps(result, allow_nan=False)


@native
@pytest.mark.parametrize("analysis", [
    {"kind": "op", "settings": {}},
    {"kind": "dc", "settings": {"source": "source", "start": 0, "stop": 5, "step": 1}},
    {"kind": "ac", "settings": {"start_frequency": 1e6, "stop_frequency": 100e6, "points": 9, "variation": "lin"}},
    {"kind": "transient", "settings": {"time_step": .1e-9, "max_step": .1e-9, "stop_time": 100e-9}},
])
def test_grounded_variant_matches_explicit_grounded_shield_in_real_solver(analysis):
    source = {"dc": 1, "ac_magnitude": 1, "waveform": {"kind": "sin", "offset": 0, "amplitude": 1, "frequency": 40e6}}
    explicit = execute_circuit(circuit(source=source), analysis)
    grounded = execute_circuit(circuit(source=source, kind="COAX_GND"), analysis)
    assert grounded["converged"] and explicit["converged"]
    assert grounded["axis"]["values"] == pytest.approx(explicit["axis"]["values"])
    if analysis["kind"] == "op":
        for name in ("V(coax1:input)", "V(coax1:output)", "I(coax1:input)", "I(coax1:output)", "P(coax1) net input"):
            assert grounded["summary"][name] == pytest.approx(explicit["summary"][name], rel=1e-10, abs=1e-12)
    else:
        expected = {s["name"]: s["values"] for s in explicit["signals"] if "coax1:" in s["name"] or s["name"] == "P(coax1) net input"}
        actual = {s["name"]: s["values"] for s in grounded["signals"] if s["name"] in expected}
        assert actual.keys() == expected.keys()
        for name in expected:
            assert actual[name] == pytest.approx(expected[name], rel=1e-10, abs=1e-12)
    assert "coax1.n1" not in grounded["diagnostics"]["node_map"]
    assert "coax1.n2" not in grounded["diagnostics"]["node_map"]

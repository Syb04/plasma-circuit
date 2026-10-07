from __future__ import annotations

import ctypes.util
import json

import numpy as np
import pytest

from app.engine import CircuitError, SolverError, build_netlist, execute_circuit, simulate_netlist


def graph(parts):
    """Explicitly wire component endpoints sharing a symbolic test net."""
    components = []
    nets = {}
    for cid, kind, pins, parameters in parts:
        components.append({"id": cid, "kind": kind, "ports": list(pins), "parameters": parameters})
        for port, net in pins.items():
            nets.setdefault(net, []).append({"component_id": cid, "port": port})
    components.append({"id": "gnd", "kind": "GND", "ports": ["g"], "parameters": {}})
    nets.setdefault("0", []).append({"component_id": "gnd", "port": "g"})
    wires = []
    for ends in nets.values():
        for target in ends[1:]:
            wires.append({"id": f"w{len(wires)}", "source": ends[0], "target": target})
    return {"schema_version": 1, "name": "test", "components": components, "wires": wires, "models": [], "parameters": {}}


def run(document, kind="op", **settings):
    return execute_circuit(document, {"kind": kind, "settings": settings})


def value_at(result, name, t):
    return float(np.interp(t, result["x"], result["vectors"][name]))


def node_of(result, endpoint):
    return result["diagnostics"]["node_map"][endpoint]


SOLVER_PRESENT = bool(ctypes.util.find_library("ngspice"))
requires_solver = pytest.mark.skipif(not SOLVER_PRESENT, reason="real ngspice shared library required")


def test_topology_does_not_infer_screen_intersections():
    document = graph([
        ("v1", "V", {"p": "in", "n": "0"}, {"dc": 5}),
        ("r1", "R", {"p": "in", "n": "out"}, {"value": "1k"}),
        ("r2", "R", {"p": "out", "n": "0"}, {"value": "1k"}),
    ])
    for component in document["components"]:
        component["position"] = {"x": 0, "y": 0}
    netlist = build_netlist(document)
    assert "r1 n1 n2 1000" in netlist
    assert "r2 n2 0 1000" in netlist
    assert netlist.endswith(".end\n")


def test_model_commands_cannot_access_files():
    document = graph([("d1", "D", {"p": "a", "n": "0"}, {"model": "unsafe"})])
    document["models"] = [{"name": "unsafe", "definition": ".model unsafe D(Is=1e-14)\n.include /etc/passwd"}]
    with pytest.raises(CircuitError, match="コマンド"):
        build_netlist(document)


def test_missing_ground_and_invalid_wire_fail_before_solver():
    document = graph([("r1", "R", {"p": "a", "n": "0"}, {"value": 1000})])
    document["wires"][0]["target"]["port"] = "missing"
    with pytest.raises(CircuitError, match="存在"):
        build_netlist(document)
    document["wires"] = []
    document["components"] = document["components"][:-1]
    with pytest.raises(CircuitError, match="GND"):
        build_netlist(document)


def test_all_catalog_native_elements_compile():
    from app.catalog import get_catalog
    catalog = get_catalog()["components"]
    assert {"R", "C", "L", "K", "V", "I", "E", "F", "G", "H", "B", "S", "W", "D", "Q", "J", "Z", "T", "O", "U", "Y", "P", "X", "EDD", "GND", "JUNCTION"} <= {c["kind"] for c in catalog}
    for entry in catalog:
        kind = entry["kind"]
        if kind in {"GND", "JUNCTION"}:
            continue
        params = entry["parameters"].copy()
        support = [("v1", "V", {"p": "a", "n": "0"}, {"dc": 1}), ("l1", "L", {"p": "a", "n": "b"}, {"value": 1e-3}), ("l2", "L", {"p": "b", "n": "0"}, {"value": 1e-3})]
        candidate = ("device", kind, {p: f"pin_{i}" for i, p in enumerate(entry["ports"])}, params)
        document = graph(support + [candidate])
        if kind == "X":
            document["models"] = [{"name": "SUBCKT", "definition": ".subckt SUBCKT p n\nR1 p n 1000\n.ends SUBCKT"}]
        netlist = build_netlist(document)
        assert netlist.endswith(".end\n"), kind


@requires_solver
def test_resistor_divider_and_dc_sweep():
    document = graph([
        ("supply", "V", {"p": "in", "n": "0"}, {"dc": 5}),
        ("r1", "R", {"p": "in", "n": "out"}, {"value": 1000}),
        ("r2", "R", {"p": "out", "n": "0"}, {"value": 1000}),
    ])
    result = run(document)
    assert result["vectors"][node_of(result, "r2.p")][0] == pytest.approx(2.5)
    assert result["vectors"]["i(v_supply)"][0] == pytest.approx(-0.0025)
    swept = run(document, "dc", source="supply", start=0, stop=5, step=1)
    assert swept["vectors"][node_of(swept, "r2.p")] == pytest.approx([0, 0.5, 1, 1.5, 2, 2.5])
    json.dumps(swept, allow_nan=False)


@requires_solver
def test_rc_transient_matches_analytic_response():
    document = graph([
        ("v1", "V", {"p": "in", "n": "0"}, {"dc": 0, "waveform": {"kind": "pulse", "initial": 0, "pulsed": 1, "rise": 1e-9, "fall": 1e-9, "width": 0.01, "period": 0.02}}),
        ("r1", "R", {"p": "in", "n": "out"}, {"value": 1000}),
        ("c1", "C", {"p": "out", "n": "0"}, {"value": 1e-6}),
    ])
    result = run(document, "transient", time_step=1e-5, stop_time=0.005)
    assert value_at(result, node_of(result, "c1.p"), 0.001) == pytest.approx(1 - np.exp(-1), rel=1e-3)


@requires_solver
def test_rc_ac_magnitude_and_phase():
    document = graph([
        ("v1", "V", {"p": "in", "n": "0"}, {"dc": 0, "ac_magnitude": 1}),
        ("r1", "R", {"p": "in", "n": "out"}, {"value": 1000}),
        ("c1", "C", {"p": "out", "n": "0"}, {"value": 1e-6}),
    ])
    result = run(document, "ac", start_frequency=100, stop_frequency=200, points=2, variation="lin")
    node = node_of(result, "c1.p")
    expected = 1 / (1 + 2j * np.pi * 100 * 0.001)
    assert result["vectors"][node][0] == pytest.approx(abs(expected))
    phase = next(signal for signal in result["signals"] if signal["name"] == f"V({node}) phase")
    assert phase["values"][0] == pytest.approx(np.angle(expected, deg=True))
    assert result["complex_vectors"][node]["imag"][0] == pytest.approx(expected.imag)


@requires_solver
def test_edd_linear_resistor_and_capacitor_match_native():
    document = graph([
        ("v1", "V", {"p": "in", "n": "0"}, {"dc": 0, "ac_magnitude": 1}),
        ("r1", "R", {"p": "in", "n": "out"}, {"value": 1000}),
        ("edd1", "EDD", {"p1": "out", "n1": "0"}, {"parameters": {"R": 1000, "C0": 1e-6}, "branches": [{"positive": "p1", "negative": "n1", "current": "V1/R", "charge": "C0*V1"}]}),
    ])
    result = run(document, "ac", start_frequency=100, stop_frequency=200, points=2, variation="lin")
    expected = 1 / (2 + 2j * np.pi * 100 * 0.001)
    assert result["vectors"][node_of(result, "edd1.p1")][0] == pytest.approx(abs(expected), rel=1e-5)
    op = run({**document, "components": [{**c, "parameters": {**c["parameters"], "dc": 5}} if c["id"] == "v1" else c for c in document["components"]]})
    assert op["vectors"][node_of(op, "edd1.p1")][0] == pytest.approx(2.5)


@requires_solver
def test_edd_nonlinear_charge_derivative():
    document = graph([
        ("v1", "V", {"p": "a", "n": "0"}, {"dc": 0, "waveform": {"kind": "pwl", "points": [[0, 0], [0.001, 1], [0.002, 1]]}}),
        ("edd1", "EDD", {"p1": "a", "n1": "0"}, {"parameters": {"C0": 1e-6}, "branches": [{"positive": "p1", "negative": "n1", "current": "0", "charge": "C0*V1^2"}]}),
    ])
    result = run(document, "transient", time_step=1e-5, stop_time=0.002)
    # Q = C V^2, dV/dt=1000 V/s; source current is negative load current.
    assert value_at(result, "i(v1)", 0.0005) == pytest.approx(-0.001, rel=2e-3)
    charge = next(s for s in result["signals"] if s["name"] == "Q(edd1:branch1)")
    total = next(s for s in result["signals"] if s["name"] == "I(edd1:branch1) total")
    assert charge["unit"] == "C" and total["unit"] == "A"
    assert np.interp(.0005, result["axis"]["values"], charge["values"]) == pytest.approx(2.5e-7, rel=2e-3)
    assert np.interp(.0005, result["axis"]["values"], total["values"]) == pytest.approx(.001, rel=2e-3)


@requires_solver
def test_edd_cross_branch_voltage_and_current_charge():
    document = graph([
        ("v1", "V", {"p": "a", "n": "0"}, {"dc": 0, "waveform": {"kind": "pwl", "points": [[0, 0], [0.001, 1], [0.002, 1]]}}),
        ("v2", "V", {"p": "b", "n": "0"}, {"dc": 0, "waveform": {"kind": "pwl", "points": [[0, 0], [0.001, 2], [0.002, 2]]}}),
        ("edd1", "EDD", {"p1": "a", "n1": "0", "p2": "b", "n2": "0"}, {"parameters": {"R": 1000, "C0": 1e-6, "tau": 0.001}, "branches": [{"positive": "p1", "negative": "n1", "current": "V1/R", "charge": "C0*(V1-V2)+tau*I2"}, {"positive": "p2", "negative": "n2", "current": "V2/R", "charge": "C0*(V2-V1)"}]}),
    ])
    result = run(document, "transient", time_step=1e-5, stop_time=0.002)
    # At t=.5 ms: I1=.5mA; Q1dot=-1mA+2mA=1mA.
    assert value_at(result, "i(v1)", 0.0005) == pytest.approx(-0.0015, rel=1e-3)
    assert value_at(result, "i(v2)", 0.0005) == pytest.approx(-0.002, rel=1e-3)


@requires_solver
@pytest.mark.parametrize("power", ["V1^3", "pow(V1,3)", "V1^exponent"])
def test_edd_odd_charge_power_preserves_negative_voltage_and_derivative(power):
    document = graph([
        ("v1", "V", {"p": "a", "n": "0"}, {"dc": -1, "waveform": {"kind": "pwl", "points": [[0, -1], [.001, 1], [.002, 1]]}}),
        ("edd1", "EDD", {"p1": "a", "n1": "0"}, {"parameters": {"C0": 1e-6, "alpha": 1e-7, "exponent": 3}, "branches": [{"positive": "p1", "negative": "n1", "current": "0", "charge": "C0*V1+alpha*" + power}]}),
    ])
    result = run(document, "transient", time_step=1e-6, stop_time=.002)
    # At -.5 V the cubic charge is negative and its differential capacitance
    # is +3*alpha*V^2. ngspice's unqualified pow(-x,3) would reverse this sign.
    assert value_at(result, "edd_edd1_q1", .00025) == pytest.approx(-5.125e-7, rel=1e-3)
    assert value_at(result, "i(v1)", .00025) == pytest.approx(-.00215, rel=1e-3)


@requires_solver
def test_floating_edd_branch_voltage_uses_complex_terminal_difference():
    document = graph([
        ("v1", "V", {"p": "a", "n": "0"}, {"dc": 7, "ac_magnitude": 1, "ac_phase": 0}),
        ("v2", "V", {"p": "b", "n": "0"}, {"dc": 2, "ac_magnitude": 1, "ac_phase": 90}),
        ("edd1", "EDD", {"p1": "a", "n1": "b"}, {"parameters": {"R": 1000, "C0": 1e-6}, "branches": [{"positive": "p1", "negative": "n1", "current": "V1/R", "charge": "C0*V1"}]}),
    ])
    op = run(document)
    assert op["summary"]["V(edd1:branch1)"] == pytest.approx(5)
    assert op["summary"]["Q(edd1:branch1)"] == pytest.approx(5e-6)
    ac = run(document, "ac", start_frequency=100, stop_frequency=200, points=2, variation="lin")
    signals = {s["name"]: s for s in ac["signals"]}
    assert signals["V(edd1:branch1) magnitude"]["values"][0] == pytest.approx(np.sqrt(2))
    assert signals["V(edd1:branch1) phase"]["values"][0] == pytest.approx(-45)
    assert signals["Q(edd1:branch1) magnitude"]["values"][0] == pytest.approx(np.sqrt(2)*1e-6)
    assert signals["Q(edd1:branch1) phase"]["values"][0] == pytest.approx(-45)


@requires_solver
def test_invalid_native_model_is_solver_error_not_fallback():
    with pytest.raises(SolverError):
        simulate_netlist("* invalid\nV1 a 0 1\nD1 a 0 MISSING\n.end\n", {"kind": "op", "settings": {}})


def test_requested_sample_limit_and_native_injection_rejected():
    with pytest.raises(CircuitError, match="上限"):
        simulate_netlist("* no solver", {"kind": "transient", "settings": {"time_step": 1e-12, "stop_time": 1}})
    document = graph([("r1", "R", {"p": "a", "n": "0"}, {"value": 1000, "raw": "\n.control\nshell cat /etc/passwd"})])
    with pytest.raises(CircuitError, match="制御"):
        build_netlist(document)


@requires_solver
@pytest.mark.parametrize("kind,fixture,expected_node,expected", [
    ("D", "V1 a 0 1\nR1 a b 1000\nD1 b 0 DDEFAULT", "b", 0.6294754),
    ("Q", "V1 c 0 5\nV2 b 0 .7\nQ1 c b 0 NPNDEFAULT", "b", .7),
    ("J", "V1 d 0 1\nV2 g 0 -1\nJ1 d g 0 NJFETDEFAULT", "d", 1),
    ("Z", "V1 d 0 1\nV2 g 0 -1\nZ1 d g 0 NMESDEFAULT", "d", 1),
    ("S", "V1 a 0 1\nR1 a b 1000\nV2 c 0 1\nS1 b 0 c 0 SWDEFAULT", "b", 1/1001),
    ("W", "V1 a 0 1\nR1 a b 1000\nV2 c 0 1\nR2 c 0 1000\nW1 b 0 V2 CSWDEFAULT", "b", 1),
    ("E", "V1 c 0 1\nE1 b 0 c 0 2\nR1 b 0 1000", "b", 2),
    ("G", "V1 c 0 1\nG1 b 0 c 0 .001\nR1 b 0 1000", "b", -1),
    ("F", "V1 c 0 1\nR1 c 0 1000\nF1 b 0 V1 1\nR2 b 0 1000", "b", 1),
    ("H", "V1 c 0 1\nR1 c 0 1000\nH1 b 0 V1 1000\nR2 b 0 1000", "b", -1),
    ("B", "V1 a 0 2\nB1 b 0 V={v(a)^2}\nR1 b 0 1000", "b", 4),
    ("I", "I1 0 a .001\nR1 a 0 1000", "a", 1),
    ("T", "V1 a 0 1\nT1 a 0 b 0 Z0=50 TD=1n\nR1 b 0 50", "b", 1),
    ("O", "V1 a 0 1\nO1 a 0 b 0 LTRDEFAULT\nR1 b 0 50", "b", 50/51),
    ("U", "V1 a 0 1\nU1 a b 0 URCDEFAULT L=1 N=5\nR1 b 0 50", "b", 1/3),
    ("Y", "V1 a 0 1\nY1 a 0 b 0 TXLDEFAULT\nR1 b 0 50", "b", 50/51),
    ("P", "V1 a 0 1\nP1 a 0 b 0 CPLDEFAULT\nR1 b 0 50", "b", 50/51),
    ("X", "V1 a 0 1\nX1 a b DIVIDER\n.subckt DIVIDER in out\nR1 in out 1000\nR2 out 0 1000\n.ends DIVIDER", "b", .5),
])
def test_native_device_real_operating_points(kind, fixture, expected_node, expected):
    from app.catalog import BUILTIN_MODELS
    netlist = "* native " + kind + "\n.options savecurrents\n" + fixture + "\n" + "\n".join(BUILTIN_MODELS.values()) + "\n.end\n"
    result = simulate_netlist(netlist, {"kind": "op", "settings": {}})
    assert result["vectors"][expected_node][0] == pytest.approx(expected, rel=1e-5)


@requires_solver
def test_coupled_inductors_real_ac():
    netlist = "* coupled\nV1 a 0 DC 0 AC 1\nR1 a b 100\nL1 b 0 1m\nL2 c 0 1m\nR2 c 0 100\nK1 L1 L2 .9\n.end\n"
    result = simulate_netlist(netlist, {"kind": "ac", "settings": {"start_frequency": 1e6, "stop_frequency": 2e6, "points": 2, "variation": "lin"}})
    assert result["vectors"]["c"][0] == pytest.approx(0.0744495212)


@requires_solver
def test_internal_plasma_averages_use_full_samples_and_ui_remains_bounded(monkeypatch):
    import app.engine as engine
    monkeypatch.setattr(engine, "MAX_SAMPLES", 31)
    netlist = "* RF averaging\nV1 a 0 SIN(0 1 1000)\nR1 a 0 1000\n.end\n"
    settings = {"time_step": 1e-5, "stop_time": .01, "retain_all_samples": True}
    result = simulate_netlist(netlist, {"kind": "transient", "settings": settings})
    assert len(result["x"]) > 31
    assert len(result["vectors"]["a"]) == len(result["x"])
    assert len(result["axis"]["values"]) == len(result["signals"][0]["values"]) == 31
    average = np.trapz(result["vectors"]["a"], result["x"]) / .01
    assert abs(average) < 1e-4
    document = graph([("v1", "V", {"p": "a", "n": "0"}, {"dc": 0, "waveform": {"kind": "sin", "frequency": 1000, "amplitude": 1}}), ("r1", "R", {"p": "a", "n": "0"}, {"value": 1000})])
    public = run(document, "transient", **settings)
    assert len(public["x"]) == len(public["axis"]["values"]) == 31

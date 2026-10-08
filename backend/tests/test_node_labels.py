"""Electrical label scope, immutable signal IDs and real-solver regressions."""
from __future__ import annotations

import copy
import ctypes.util
from dataclasses import asdict

import numpy as np
import pytest
from pydantic import ValidationError

from app.engine import CircuitError, _topology, build_netlist, execute_circuit
from app.external_rf import solve_external_ccp, template_document
from app.node_labels import apply_node_labels, endpoint_groups, resolve_node_labels
from app.plasma import CCPSettings
from app.schemas import CircuitDocument
from test_api import client, run
from test_engine import graph

requires_solver = pytest.mark.skipif(not ctypes.util.find_library("ngspice"), reason="real ngspice required")


def divider():
    return graph([
        ("v1", "V", {"p": "in", "n": "0"}, {"dc": 5, "ac_magnitude": 1,
            "waveform": {"kind": "sin", "offset": 0, "amplitude": 2, "frequency": 1000}}),
        ("r1", "R", {"p": "in", "n": "out"}, {"value": 1000}),
        ("r2", "R", {"p": "out", "n": "0"}, {"value": 2000}),
    ])


def label(cid, port, name):
    return {"component_id": cid, "port": port, "name": name}


def test_legacy_documents_serialize_without_new_empty_fields():
    model = CircuitDocument.model_validate(divider())
    assert model.node_labels == []
    assert "node_labels" not in model.model_dump()
    assert "node_labels" not in CircuitDocument.model_validate({**divider(), "node_labels": []}).model_dump()


def test_label_is_shared_by_actual_wires_and_never_by_geometrical_overlap():
    doc = divider()
    for component in doc["components"]:
        component["position"] = {"x": 0, "y": 0}
    doc["node_labels"] = [label("r1", "n", "  電極 (RF)  ")]
    saved = CircuitDocument.model_validate(doc).model_dump()
    nets = _topology(saved)[2]
    assert resolve_node_labels(saved, nets) == {nets[("r1", "n")]: "電極 (RF)"}
    assert nets[("r1", "n")] == nets[("r2", "p")]
    assert nets[("r1", "p")] != nets[("r1", "n")]
    assert build_netlist(doc) == build_netlist(divider())
    doc["node_labels"][0]["name"] = '.include malicious-file <script> " : 日本語'
    assert build_netlist(doc) == build_netlist(divider())


@pytest.mark.parametrize("name", ["", "  ", "a" * 101, "RF\n電極", "x\x00y", "x\x7fy", "x\u2028y", "x\u2029y", 123, None])
def test_invalid_label_names_rejected(name):
    with pytest.raises(ValidationError):
        CircuitDocument.model_validate({**divider(), "node_labels": [label("r1", "n", name)]})


def test_one_hundred_unicode_codepoints_are_allowed():
    value = "⚡" * 100
    saved = CircuitDocument.model_validate({**divider(), "node_labels": [label("r1", "n", value)]})
    assert saved.node_labels[0].name == value


@pytest.mark.parametrize("labels", [
    [label("missing", "p", "出力")], [label("r1", "missing", "出力")],
    [label("r1", "n", "出力"), label("r1", "n", "出力")],
    [label("r1", "n", "出力"), label("r2", "p", "別名")],
    [label("r1", "n", "出力"), label("v1", "p", "出力")],
    [{**label("r1", "n", "出力"), "unexpected": True}],
])
def test_missing_endpoints_and_ambiguous_names_rejected(labels):
    with pytest.raises(ValidationError):
        CircuitDocument.model_validate({**divider(), "node_labels": labels})


def test_direct_solver_compilation_rejects_label_conflicts_before_ngspice():
    doc = divider()
    doc["node_labels"] = [label("r1", "n", "出力"), label("r2", "p", "別名")]
    with pytest.raises(CircuitError, match="同じ電気的ノード"):
        build_netlist(doc)


def test_coax_shields_and_all_ground_symbols_share_label_scope():
    doc = divider()
    doc["components"] += [
        {"id": "coax", "kind": "COAX", "ports": ["p1", "n1", "p2", "n2"], "parameters": {}},
        {"id": "ground2", "kind": "GND", "ports": ["g"], "parameters": {}},
    ]
    groups = endpoint_groups(doc)
    assert groups[("coax", "n1")] == groups[("coax", "n2")]
    assert groups[("gnd", "g")] == groups[("ground2", "g")]
    assert groups[("coax", "p1")] != groups[("coax", "p2")]
    doc["node_labels"] = [label("coax", "n1", "シールド"), label("coax", "n2", "別名")]
    with pytest.raises(ValidationError, match="同じ電気的ノード"):
        CircuitDocument.model_validate(doc)


def test_labels_can_be_saved_before_ground_or_wiring_is_added():
    doc = {"name": "構築中", "components": [{"id": "r1", "kind": "R", "ports": ["p", "n"]}],
           "node_labels": [label("r1", "p", "測定予定")], "wires": []}
    assert CircuitDocument.model_validate(doc).node_labels[0].name == "測定予定"


def test_alias_collision_with_unlabeled_trace_is_disambiguated():
    doc = {"node_labels": [label("v1", "p", "n2")]}
    result = {"signals": [{"name": "V(n1)", "unit": "V", "values": [1]},
                           {"name": "V(n2)", "unit": "V", "values": [2]}]}
    apply_node_labels(result, doc, {("v1", "p"): "n1"})
    assert [s["display_name"] for s in result["signals"]] == ["V(n2) [V(n1)]", "V(n2) [V(n2)]"]
    assert [s["name"] for s in result["signals"]] == ["V(n1)", "V(n2)"]


@requires_solver
@pytest.mark.parametrize("analysis", [
    {"kind": "transient", "settings": {"time_step": 1e-5, "stop_time": .002}},
    {"kind": "ac", "settings": {"start_frequency": 100, "stop_frequency": 10000, "points": 3, "variation": "lin"}},
    {"kind": "dc", "settings": {"source": "v1", "start": 0, "stop": 5, "step": 1}},
    {"kind": "op", "settings": {}},
])
def test_real_solver_labels_preserve_netlist_signal_ids_and_values(analysis):
    doc = divider()
    baseline = execute_circuit(doc, analysis)
    doc["node_labels"] = [label("v1", "p", "RF入力"), label("r1", "n", "電極")]
    labeled = execute_circuit(doc, analysis)
    assert labeled["netlist"] == baseline["netlist"]
    assert labeled["summary"] == baseline["summary"]
    assert labeled["vectors"] == baseline["vectors"]
    assert [{key: value for key, value in s.items() if key != "display_name"} for s in labeled["signals"]] == baseline["signals"]
    out = labeled["diagnostics"]["node_map"]["r1.n"]
    assert labeled["diagnostics"]["node_labels"][out] == "電極"
    if analysis["kind"] == "ac":
        assert labeled["complex_vectors"] == baseline["complex_vectors"]
        assert {s["display_name"] for s in labeled["signals"] if s["name"].startswith(f"V({out})")} == {"V(電極) magnitude", "V(電極) phase"}
    else:
        for signal in labeled["signals"]:
            if signal["name"] == f"V({out})":
                assert signal["display_name"] == "V(電極)"


@requires_solver
def test_named_ground_has_an_exact_zero_voltage_trace():
    doc = divider()
    doc["node_labels"] = [label("gnd", "g", "接地")]
    result = execute_circuit(doc, {"kind": "transient", "settings": {"time_step": 1e-5, "stop_time": .002}})
    signal = next(s for s in result["signals"] if s["name"] == "V(0)")
    assert signal["display_name"] == "V(接地)"
    assert len(signal["values"]) == len(result["axis"]["values"])
    assert not any(signal["values"])


@requires_solver
def test_external_ccp_exports_named_absolute_nodes_without_renaming_physical_signals():
    settings = CCPSettings(rf_peak_voltage=100, cycles=24, points_per_cycle=128)
    doc = template_document(settings, {"source_resistance_ohm": 50})
    plasma = next(c for c in doc["components"] if c["kind"] == "PLASMA")
    doc["node_labels"] = [label(plasma["id"], "p", "駆動電極")]
    result = solve_external_ccp(settings, doc, asdict(settings))
    net = result["diagnostics"]["node_map"][plasma["id"] + ".p"]
    named = next(s for s in result["signals"] if s["name"] == f"V({net})")
    cathode = next(s for s in result["signals"] if s["name"] == "V(cathode)")
    assert named["display_name"] == "V(駆動電極)"
    assert len(named["values"]) == len(result["axis"]["values"])
    np.testing.assert_allclose(named["values"], cathode["values"], rtol=1e-12, atol=1e-12)
    assert result["converged"]


def test_api_roundtrip_revision_snapshot_and_alias_csv(client):
    doc = divider()
    doc["node_labels"] = [label("r1", "n", "  電極  ")]
    created = client.post('/api/circuits', json={"employee_id": "000-node-check", "document": doc})
    assert created.status_code == 201
    saved = created.json()
    assert saved["document"]["node_labels"] == [label("r1", "n", "電極")]
    queued = run(client, saved)
    updated = copy.deepcopy(saved["document"])
    updated["node_labels"][0]["name"] = "新しい名前"
    response = client.put('/api/circuits/' + saved["id"], json={"employee_id": "000-node-check", "expected_revision": 1, "document": updated})
    assert response.status_code == 200
    assert client.get('/api/runs/' + queued["id"]).json()["snapshot"]["node_labels"] == [label("r1", "n", "電極")]
    from app import database as db
    with db.session() as session:
        record = session.get(db.SimulationRun, queued["id"])
        record.status = "succeeded"
        record.result_compressed = db.compress_result({"axis": {"name": "time", "unit": "s", "values": [0]},
            "signals": [{"name": "V(n2)", "display_name": "V(電極)", "unit": "V", "values": [3]}]})
        session.commit()
    csv = client.get('/api/runs/' + queued["id"] + '/export.csv')
    assert csv.status_code == 200 and "V(電極) [V]" in csv.text


def test_api_rejects_missing_or_conflicting_label_endpoints(client):
    for labels in [[label("missing", "p", "出力")], [label("r1", "n", "出力"), label("r2", "p", "別名")]]:
        response = client.post('/api/circuits', json={"employee_id": "000-node-check", "document": {**divider(), "node_labels": labels}})
        assert response.status_code == 422

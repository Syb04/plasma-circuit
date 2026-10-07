"""Workflow invariants with a durable SQLite DB and an isolated mock queue."""
from __future__ import annotations

import copy
import csv
import io
import json
import math

import numpy as np
import pytest

from app import api, database as db, worker, benchmarks, analysis_package, studies
from app.schemas import CreateRun, CreateStudy, EmployeeRequest, ImportPackage, SaveCircuit, StudyAxis, UpdateCircuit
from test_api import FakeQueue, client, document, save, solver  # noqa: F401 - shared isolated fixture


def create_study(client, circuit, axes=None):
    return client.post("/api/studies", json={"employee_id": "000123", "name": "Pressure sweep",
        "circuit_id": circuit["id"], "expected_revision": circuit["revision"],
        "analysis": {"kind": "ccp", "settings": {"pressure_pa": 1}},
        "axes": axes or [{"path": "analysis.settings.pressure_pa", "values": [1, 2, 3]}]})


def succeed(run_id, result=None):
    with db.session() as session:
        run = session.get(db.SimulationRun, run_id)
        run.status = "succeeded"
        run.result_compressed = db.compress_result(result or {"summary": {"electron_density_m3": 2e16}, "converged": True})
        run.finished_at = db.utcnow()
        session.commit()


def test_study_atomic_snapshots_and_case_coordinates(client):
    circuit = save(client)
    response = create_study(client, circuit, [
        {"path": "analysis.settings.pressure_pa", "values": [1, 2]},
        {"path": "document.components.r1.parameters.value", "values": [100, 200]},
    ])
    assert response.status_code == 202, response.text
    study = response.json()
    assert study["counts"]["queued"] == 4
    assert [case["index"] for case in study["cases"]] == list(range(4))
    assert len(client.queue.jobs) == 4
    with db.session() as session:
        assert session.query(db.StudyCase).count() == session.query(db.StudyAttempt).count() == 4
        snapshots = session.query(db.SimulationRun).all()
        assert {run.circuit_revision for run in snapshots} == {1}
        assert [run.snapshot["components"][0]["parameters"]["value"] for run in snapshots] == [100, 200, 100, 200]
        assert [run.analysis["settings"]["pressure_pa"] for run in snapshots] == [1, 1, 2, 2]
        original = session.get(db.Circuit, circuit["id"])
        original.document = {**original.document, "name": "Later revision"}
        original.revision = 2
        session.commit()
    persisted = client.get(f"/api/studies/{study['id']}").json()
    assert persisted["snapshot"]["name"] == circuit["name"]
    assert create_study(client, circuit).status_code == 409


@pytest.mark.parametrize("axes", [
    [{"path": "analysis.settings.pressure_pa", "values": list(range(11))}, {"path": "analysis.settings.rf_peak_voltage", "values": list(range(10))}],
    [{"path": "document.models.custom.definition", "values": [1]}],
    [{"path": "analysis.settings.__class__", "values": [1]}],
    [{"path": "document.components.missing.parameters.value", "values": [1]}],
    [{"path": "analysis.settings.cycles", "values": [16.5]}],
    [{"path": "analysis.settings.pressure_pa", "values": [1]}, {"path": "analysis.settings.pressure_pa", "values": [2]}],
])
def test_study_rejects_unsafe_or_unbounded_axes_before_writes(client, axes):
    response = create_study(client, save(client), axes)
    assert response.status_code == 422, response.text
    with db.session() as session:
        assert session.query(db.Study).count() == session.query(db.SimulationRun).count() == 0
    assert not client.queue.jobs


def test_batch_enqueue_failure_retains_every_case_and_resumes_failed_only(client, monkeypatch):
    circuit = save(client)
    real_enqueue = worker.enqueue_run
    calls = []
    def intermittent(run_id):
        calls.append(run_id)
        if len(calls) == 2:
            raise RuntimeError("queue disconnected")
        return real_enqueue(run_id)
    monkeypatch.setattr(worker, "enqueue_run", intermittent)
    study = create_study(client, circuit).json()
    assert len(calls) == 3
    first, failed, third = study["cases"]
    assert failed["status"] == "failed"
    succeed(first["run_id"])
    succeed(third["run_id"])
    # New circuit content is irrelevant to a retry of the original case.
    with db.session() as session:
        record = session.get(db.Circuit, circuit["id"])
        record.document = {**record.document, "name": "changed"}
        record.revision = 2
        session.commit()
    resumed = client.post(f"/api/studies/{study['id']}/resume", json={"employee_id": "000456"})
    assert resumed.status_code == 202, resumed.text
    resumed_cases = resumed.json()["cases"]
    assert resumed_cases[0]["run_id"] == first["run_id"]
    assert resumed_cases[2]["run_id"] == third["run_id"]
    retry = resumed_cases[1]
    assert retry["run_id"] != failed["run_id"]
    assert [a["status"] for a in retry["attempts"]] == ["failed", "queued"]
    assert [a["number"] for a in retry["attempts"]] == [1, 2]
    with db.session() as session:
        old = session.get(db.SimulationRun, failed["run_id"])
        new = session.get(db.SimulationRun, retry["run_id"])
        assert old.snapshot == new.snapshot and old.analysis == new.analysis
        assert new.employee_id == "000456" and old.employee_id == "000123"
    assert client.post(f"/api/studies/{study['id']}/resume", json={"employee_id": "x"}).status_code == 409
    exported = client.get(f"/api/studies/{study['id']}/export.csv")
    rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
    assert len(rows) == 4 and "electron_density_m3" in rows[0]


def test_cancel_study_is_durable_and_can_resume_without_erasing_history(client):
    study = create_study(client, save(client)).json()
    canceled = client.post(f"/api/studies/{study['id']}/cancel").json()
    assert canceled["status"] == "canceled" and canceled["counts"]["canceled"] == 3
    assert all(client.queue.jobs[c["run_id"]].state == "canceled" for c in canceled["cases"])
    resumed = client.post(f"/api/studies/{study['id']}/resume", json={"employee_id": "001"}).json()
    assert resumed["counts"]["queued"] == 3
    assert all(len(c["attempts"]) == 2 and c["attempts"][0]["status"] == "canceled" for c in resumed["cases"])


def test_reference_units_uncertainty_and_conditions_are_explicit(client):
    study = create_study(client, save(client)).json()
    run_id = study["cases"][0]["run_id"]
    succeed(run_id)
    response = client.post("/api/benchmarks", json={"name": "Measured density", "material": "Ar",
        "measurement_definition": "Cycle-averaged electron density by interferometry",
        "frequency_hz": 40e6, "pressure_pa": 2, "provenance": {"doi": "10.example/reference"},
        "format": "csv", "data": "metric,value,unit,uncertainty\nelectron_density_m3,1.9e10,cm^-3,2e9\n"})
    assert response.status_code == 201, response.text
    reference = response.json()
    assert reference["metrics"][0]["value"] == 1.9e16
    comparison = client.post(f"/api/benchmarks/{reference['id']}/compare", json={"run_id": run_id}).json()
    assert comparison["validation_status"] == "reference_comparison"
    assert comparison["comparisons"][0]["within_uncertainty"] is True
    assert math.isclose(comparison["comparisons"][0]["relative_error"], 1e15 / 1.9e16)
    assert any("pressure_pa" in warning for warning in comparison["warnings"])
    assert "validated" not in comparison


@pytest.mark.parametrize("unit,value,expected", [("MHz", 40, 40e6), ("mTorr", 10, 1.333223684),
    ("cm^-3", 1e10, 1e16), ("mV", 1200, 1.2), ("mA", 1000, 1), ("kW", 2, 2000),
    ("J", 1.602176634e-19, 1), ("mm", 3, .003), ("ns", 5, 5e-9)])
def test_reference_unit_conversion_is_deterministic(unit, value, expected):
    parsed = benchmarks.parse_reference({"name": "test", "material": "Ar", "measurement_definition": "test",
        "frequency_hz": 1, "pressure_pa": 1, "provenance": "laboratory", "format": "json",
        "data": {"metrics": [{"metric": "test", "value": value, "unit": unit}]}})
    assert parsed["metrics"][0]["value"] == pytest.approx(expected)


def test_compare_aligns_source_rf_phase_independent_of_sample_start():
    records = []
    for index, offset in enumerate([0., .125]):
        t = np.linspace(offset, 2.1 + offset, 2101)
        records.append({"id": str(index), "status": "succeeded", "snapshot": {"name": "RF"},
                        "analysis": {"kind": "ccp", "settings": {"frequency_hz": 2, "pressure_pa": index + 1}},
                        "result": {"summary": {}, "axis": {"name": "time", "unit": "s", "values": t.tolist()},
                                   "signals": [{"name": "V", "unit": "V", "values": np.sin(4*np.pi*t).tolist()}]}})
    result = benchmarks.compare_runs(records)
    assert result["phase_alignment"]["aligned"] is True
    wave = result["waveforms"]
    assert wave[0]["signals"][0]["values"] == pytest.approx(wave[1]["signals"][0]["values"], abs=1e-4)
    assert any(d["path"] == "analysis.settings.pressure_pa" for d in result["input_differences"])
    records[1]["result"]["axis"]["unit"] = "Hz"
    assert benchmarks.compare_runs(records)["phase_alignment"]["aligned"] is False


def test_portable_package_checks_hashes_and_import_never_creates_success(client):
    study = create_study(client, save(client)).json()
    run_id = study["cases"][0]["run_id"]
    succeed(run_id)
    package = client.get(f"/api/runs/{run_id}/package").json()
    assert package["original_status"] == "succeeded"
    assert any(name.startswith("data/") for name in package["runtime_config"]["implementation_sha256"])
    tampered = copy.deepcopy(package)
    tampered["input"]["document"]["components"][0]["parameters"]["value"] = 999
    rejected = client.post("/api/packages/import", json={"employee_id": "000456", "package": tampered})
    assert rejected.status_code == 422
    imported = client.post("/api/packages/import", json={"employee_id": "000456", "package": package})
    assert imported.status_code == 201, imported.text
    restored = imported.json()
    assert restored["circuit"]["id"] != package["input"]["circuit_id"]
    assert restored["circuit"]["document"] == package["input"]["document"]
    assert restored["requires_recalculation"] is True and restored["result"] is None
    assert restored["provenance"]["hashes"] == package["hashes"]
    with db.session() as session:
        assert session.query(db.SimulationRun).count() == 3
        assert session.query(db.PackageImport).count() == 1
        archived = session.query(db.PackageImport).one().package
        assert archived["result"]["converged"] is True
        assert archived["runtime_config"] == package["runtime_config"]


def test_package_rejects_content_hash_tamper_even_with_recomputed_envelope(client):
    study = create_study(client, save(client)).json()
    package = client.get(f"/api/runs/{study['cases'][0]['run_id']}/package").json()
    package["hashes"]["input_sha256"] = "0" * 64
    package.pop("package_sha256")
    package["package_sha256"] = analysis_package.digest(package)
    assert client.post("/api/packages/import", json={"employee_id": "x", "package": package}).status_code == 422


def portable_package(result=None):
    run = db.SimulationRun(id="historical-run", circuit_id="historical-circuit", circuit_revision=1,
        employee_id="001", snapshot={"name": "Portable", "components": []},
        analysis={"kind": "ccp", "settings": {}}, runtime_config={"implementation_sha256": {}},
        status="succeeded" if result is not None else "queued",
        result_compressed=None if result is None else db.compress_result(result))
    return analysis_package.export_run(run)


def test_package_model_hash_covers_actual_model_metadata():
    first = portable_package({"model_metadata": {"input_settings": {"pressure_pa": 1}}})
    second = portable_package({"model_metadata": {"input_settings": {"pressure_pa": 2}}})
    assert first["hashes"]["input_sha256"] == second["hashes"]["input_sha256"]
    assert first["hashes"]["model_sha256"] != second["hashes"]["model_sha256"]


def test_package_size_accounting_roundtrips_compact_utf8_and_bounds_export(monkeypatch):
    package = portable_package({"summary": {"description": "測定" * 200}})
    compact_size = len(analysis_package.encoded_json(package))
    assert len(json.dumps(package).encode()) > compact_size
    monkeypatch.setattr(analysis_package, "MAX_PACKAGE_BYTES", compact_size + 1)
    assert analysis_package.verify_package(package) == package
    assert analysis_package.verify_package(portable_package({"summary": {"description": "測定" * 200}}))
    with pytest.raises(ValueError, match="32 MiB"):
        portable_package({"summary": {"description": "測定" * 201}})


@pytest.mark.parametrize("malformed_result", [[1], "result", 7, {"model_metadata": []}])
def test_hash_valid_malformed_package_returns_422_before_database_writes(malformed_result):
    package = portable_package()
    package["result"] = malformed_result
    # An attacker can create their own checksums. Schema checks must still run
    # before content hashing and circuit/provenance creation.
    package.pop("package_sha256")
    package["package_sha256"] = analysis_package.digest(package)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        api.import_package(ImportPackage(employee_id="001", package=package), session=None)
    assert caught.value.status_code == 422


@pytest.mark.parametrize("field", ["value", "uncertainty"])
def test_reference_rejects_nonfinite_si_conversion(field):
    row = {"metric": "electron_density_m3", "value": 1, "unit": "cm^-3", field: 1e308}
    with pytest.raises(ValueError, match="after unit conversion"):
        benchmarks.parse_reference({"provenance": "laboratory", "format": "json", "data": [row]})


@pytest.mark.parametrize("value", [True, "1", float("inf"), float("nan")])
def test_study_axis_requires_finite_json_numbers(value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        StudyAxis(path="analysis.settings.pressure_pa", values=[value])


def test_reference_conditions_use_resolved_result_inputs():
    reference = {"metrics": [], "frequency_hz": 40e6, "pressure_pa": 1, "material": "Ar"}
    result = {"summary": {}, "model_metadata": {"input_settings": {
        "frequency_hz": 20e6, "pressure_pa": 2, "gas": "O2"}}}
    compared = benchmarks.compare_reference(result, reference, {"settings": {}})
    assert any("frequency_hz" in warning for warning in compared["warnings"])
    assert any("pressure_pa" in warning for warning in compared["warnings"])
    assert any("material" in warning for warning in compared["warnings"])


def test_phase_alignment_uses_solved_common_fundamental_and_excludes_macro():
    records = [{"id": str(index), "status": "succeeded", "snapshot": {},
        "analysis": {"kind": "ccp", "settings": {"frequency_hz": 40}},
        "result": {"kind": "ccp", "summary": {},
                   "model_metadata": {"input_settings": {"frequency_hz": 40},
                                      "rf_drive": {"fundamental_frequency_hz": 5}},
                   "axis": {"unit": "s", "values": np.linspace(0, .5, 101).tolist()},
                   "signals": [{"name": "V", "values": np.sin(np.linspace(0, 5*np.pi, 101)).tolist()}]}}
               for index in range(2)]
    compared = benchmarks.compare_runs(records)
    assert compared["phase_alignment"]["aligned"] is True
    assert compared["phase_alignment"]["frequencies_hz"] == [5, 5]
    records[1]["result"]["kind"] = "global_transient"
    compared = benchmarks.compare_runs(records)
    assert compared["phase_alignment"]["aligned"] is False
    assert compared["waveforms"][1]["axis"]["unit"] == "s"


@pytest.fixture
def workflow_database(tmp_path, monkeypatch):
    """Transaction coverage without a TestClient event-loop or local sockets."""
    from rq.exceptions import NoSuchJobError
    db.configure_database(f"sqlite:///{tmp_path / 'workflows.sqlite'}")
    db.init_database()
    queue = FakeQueue()
    monkeypatch.setenv("JOB_TIMEOUT_SECONDS", "180")
    monkeypatch.setattr(worker, "get_queue", lambda: queue)
    monkeypatch.setattr(worker, "redis_connection", lambda: queue.connection)
    def fetch(cls, job_id, connection=None):
        if job_id not in queue.jobs:
            raise NoSuchJobError(job_id)
        return queue.jobs[job_id]
    monkeypatch.setattr(api.Job, "fetch", classmethod(fetch))
    yield queue
    db.engine.dispose()


def direct_study():
    with db.session() as session:
        circuit = api.create_circuit(SaveCircuit(employee_id="000123", document=document()), session)
        request = CreateStudy(employee_id="000123", name="Pressure sweep", circuit_id=circuit["id"],
            expected_revision=1, analysis={"kind": "ccp", "settings": {"pressure_pa": 1}},
            axes=[{"path": "analysis.settings.pressure_pa", "values": [1, 2, 3]}])
        return circuit, api.create_study(request, session)


def test_direct_study_failed_enqueue_retry_preserves_inputs_and_audit(workflow_database, monkeypatch):
    real_enqueue = worker.enqueue_run
    calls = []
    def enqueue(run_id):
        calls.append(run_id)
        if len(calls) == 2:
            raise RuntimeError("queue outage")
        return real_enqueue(run_id)
    monkeypatch.setattr(worker, "enqueue_run", enqueue)
    circuit, study = direct_study()
    assert len(calls) == 3 and study["counts"]["failed"] == 1
    first, failed, third = study["cases"]
    succeed(first["run_id"])
    succeed(third["run_id"])
    with db.session() as session:
        api.update_circuit(circuit["id"], UpdateCircuit(employee_id="000456", expected_revision=1,
            document=document(value=2200)), session)
        resumed = api.resume_study(study["id"], EmployeeRequest(employee_id="000456"), session)
        retry = resumed["cases"][1]
        assert [a["status"] for a in retry["attempts"]] == ["failed", "queued"]
        assert [a["employee_id"] for a in retry["attempts"]] == ["000123", "000456"]
        original = session.get(db.SimulationRun, failed["run_id"])
        current = session.get(db.SimulationRun, retry["run_id"])
        assert original.snapshot == current.snapshot and original.analysis == current.analysis
        assert current.circuit_revision == 1
        assert resumed["cases"][0]["run_id"] == first["run_id"]
        assert resumed["cases"][2]["run_id"] == third["run_id"]
        assert session.query(db.CircuitRevision).count() == 2


def test_direct_study_cancel_and_resume_retains_terminal_history(workflow_database):
    _, study = direct_study()
    with db.session() as session:
        canceled = api.cancel_study(study["id"], session)
        assert canceled["counts"]["canceled"] == 3
        resumed = api.resume_study(study["id"], EmployeeRequest(employee_id="001"), session)
        assert resumed["counts"]["queued"] == 3
        assert all([a["status"] for a in case["attempts"]] == ["canceled", "queued"]
                   for case in resumed["cases"])
        assert session.query(db.SimulationRun).count() == 6


def test_direct_stale_revision_and_invalid_study_write_no_batch(workflow_database):
    from fastapi import HTTPException
    with db.session() as session:
        circuit = api.create_circuit(SaveCircuit(employee_id="001", document=document()), session)
        api.update_circuit(circuit["id"], UpdateCircuit(employee_id="002", expected_revision=1,
            document=document(value=2200)), session)
        with pytest.raises(HTTPException) as caught:
            api.create_run(CreateRun(employee_id="001", circuit_id=circuit["id"], expected_revision=1,
                analysis={"kind": "ccp"}), session)
        assert caught.value.status_code == 409
        with pytest.raises(HTTPException) as caught:
            api.create_study(CreateStudy(employee_id="001", circuit_id=circuit["id"], expected_revision=2,
                analysis={"kind": "ccp"}, axes=[{"path": "document.models.custom.definition", "values": [1]}]), session)
        assert caught.value.status_code == 422
        assert session.query(db.SimulationRun).count() == session.query(db.Study).count() == 0
        assert not workflow_database.jobs


def test_direct_package_import_is_historical_and_snapshot_independent(workflow_database):
    _, study = direct_study()
    run_id = study["cases"][0]["run_id"]
    succeed(run_id, {"summary": {"electron_density_m3": 2e16}, "converged": True})
    with db.session() as session:
        package = api.export_package(run_id, session)
        imported = api.import_package(ImportPackage(employee_id="000456", package=package), session)
        assert imported["requires_recalculation"] is True and imported["result"] is None
        assert imported["verification"] == {"hashes_verified": True, "authenticity_verified": False}
        assert session.query(db.SimulationRun).count() == 3
        archived = session.query(db.PackageImport).one()
        assert archived.package == package and archived.employee_id == "000456"
        circuit = imported["circuit"]
        api.update_circuit(circuit["id"], UpdateCircuit(employee_id="000456", expected_revision=1,
            document=document(value=999)), session)
        session.refresh(archived)
        assert archived.package["input"]["document"]["components"][0]["parameters"]["value"] == 1000


def authored_plasma_document(waveform=None):
    from test_engine import graph
    return graph([("source", "V", {"p": "in", "n": "0"}, {"waveform": waveform or {
        "kind": "rf", "frequency_hz": 40e6, "rf_peak_voltage": 250,
        "second_frequency_hz": 20e6, "second_rf_peak_voltage": 50}}),
        ("plasma", "PLASMA", {"p": "in", "n": "0"}, {})])


@pytest.mark.parametrize("kind,field,value", [
    ("rf", "frequency_hz", 20e6), ("rf", "rf_peak_voltage", 200),
    ("rf", "second_frequency_hz", 10e6), ("rf", "second_rf_peak_voltage", 25),
    ("sin", "frequency", 20e6), ("sin", "amplitude", 200),
])
def test_source_waveform_scalar_sweep_changes_only_case_snapshot(kind, field, value):
    waveform = ({"kind": "sin", "frequency": 40e6, "amplitude": 250} if kind == "sin"
                else {"kind": "rf", "frequency_hz": 40e6, "rf_peak_voltage": 250,
                      "second_frequency_hz": 20e6, "second_rf_peak_voltage": 50})
    original = authored_plasma_document(waveform)
    untouched = copy.deepcopy(original)
    path = f"document.components.source.parameters.waveform.{field}"
    cases = studies.expand_cases(original, {"kind": "ccp", "settings": {}}, [{"path": path, "values": [value]}])
    coordinates, snapshot, analysis = cases[0]
    assert original == untouched and coordinates == {path: value}
    assert snapshot["components"][0]["parameters"]["waveform"][field] == value
    assert analysis["settings"] == {}
    from app.engine import build_netlist
    assert build_netlist(snapshot) != build_netlist(original)


@pytest.mark.parametrize("field", ["frequency_hz", "rf_peak_voltage", "second_frequency_hz", "second_rf_peak_voltage",
                                   "second_phase_deg", "pulse_frequency_hz", "pulse_duty_cycle", "pulse_off_fraction"])
def test_authored_plasma_rejects_ignored_analysis_rf_sweep(field):
    with pytest.raises(ValueError, match=r"source waveform.*document.components"):
        studies.expand_cases(authored_plasma_document(), {"kind": "ccp", "settings": {}},
            [{"path": f"analysis.settings.{field}", "values": [1]}])


@pytest.mark.parametrize("component_kind,waveform,field", [
    ("R", {"kind": "rf", "frequency_hz": 40e6}, "frequency_hz"),
    ("I", {"kind": "rf", "frequency_hz": 40e6}, "frequency_hz"),
    ("V", {"kind": "pulse", "frequency": 40e6}, "frequency"),
    ("V", {"kind": "rf", "rf_peak_voltage": 250}, "frequency_hz"),
    ("V", {"kind": "sin", "frequency": "40Meg"}, "frequency"),
    ("V", {"kind": "rf", "parameters": {}}, "parameters"),
])
def test_source_waveform_sweep_rejects_unrelated_missing_and_nonnumeric_fields(component_kind, waveform, field):
    original = {"components": [{"id": "source", "kind": component_kind, "parameters": {"waveform": waveform}}]}
    with pytest.raises(ValueError):
        studies.expand_cases(original, {"kind": "ccp", "settings": {}},
            [{"path": f"document.components.source.parameters.waveform.{field}", "values": [1]}])


def test_direct_waveform_study_durably_changes_source_and_preserves_original(workflow_database):
    original = authored_plasma_document()
    with db.session() as session:
        circuit = api.create_circuit(SaveCircuit(employee_id="001", document=original), session)
        study = api.create_study(CreateStudy(employee_id="001", circuit_id=circuit["id"], expected_revision=1,
            analysis={"kind": "ccp", "settings": {}}, axes=[{
                "path": "document.components.source.parameters.waveform.rf_peak_voltage", "values": [100, 200]}]), session)
        runs = [session.get(db.SimulationRun, case["run_id"]) for case in study["cases"]]
        assert [run.snapshot["components"][0]["parameters"]["waveform"]["rf_peak_voltage"] for run in runs] == [100, 200]
        assert session.get(db.Circuit, circuit["id"]).document["components"][0]["parameters"]["waveform"]["rf_peak_voltage"] == 250
        assert study["snapshot"]["components"][0]["parameters"]["waveform"]["rf_peak_voltage"] == 250


def test_operating_point_reference_uses_declared_table_units():
    result = {"summary": {"V(out)": 1.2, "I(source)": .001, "Q(edd)": 2e-6},
              "tables": [{"rows": [{"signal": "V(out)", "unit": "V"},
                                     {"signal": "I(source)", "unit": "A"},
                                     {"signal": "Q(edd)", "unit": "C"}]}]}
    assert benchmarks.summary_metrics(result) == {
        "V(out)": (1.2, "voltage"), "I(source)": (.001, "current"), "Q(edd)": (2e-6, "charge")}


def test_package_digest_matches_javascript_binary64_roundtrip_number_fixture():
    original = {"values": [1.0, -0.0, 1e20, 1.0000000000000001e18, 1e-7, 5e-324,
                           1000000000000000128], "enabled": True}
    # Recorded JSON.stringify(JSON.parse(...)) output from Node. Keep the
    # regression Python-only so the backend image needs no JavaScript runtime.
    javascript = json.loads('{"values":[1,0,100000000000000000000,1000000000000000100,1e-7,5e-324,1000000000000000100],"enabled":true}')
    assert analysis_package.legacy_digest(original) != analysis_package.legacy_digest(javascript)
    assert analysis_package.digest(original) == analysis_package.digest(javascript)
    assert analysis_package.digest(True) != analysis_package.digest(1)
    assert analysis_package.digest(False) != analysis_package.digest(0)
    assert analysis_package.digest("1") != analysis_package.digest(1)
    assert analysis_package.digest(1e20) != analysis_package.digest(1e20 + 20000)


def test_portable_package_survives_javascript_number_rewrites_and_detects_tampering():
    original_numbers = [1.0, -0.0, 1e20, 1.0000000000000001e18, 1e-7, 5e-324]
    package = portable_package({"summary": {}, "model_metadata": {"numbers": original_numbers}})
    assert package["hash_algorithm"] == analysis_package.HASH_ALGORITHM
    rewritten = copy.deepcopy(package)
    rewritten["result"]["model_metadata"]["numbers"] = json.loads('[1,0,100000000000000000000,1000000000000000100,1e-7,5e-324]')
    rewritten["input"]["circuit_revision"] = 1.0
    assert analysis_package.verify_package(rewritten) == rewritten
    tampered = copy.deepcopy(rewritten)
    tampered["result"]["model_metadata"]["numbers"][0] = 2
    with pytest.raises(ValueError, match="checksum mismatch"):
        analysis_package.verify_package(tampered)


def test_raw_legacy_analysis_packages_remain_hash_verified():
    package = portable_package({"summary": {"value": 1.0}})
    package.pop("hash_algorithm")
    package.pop("package_sha256")
    package["hashes"] = analysis_package.content_hashes(package, analysis_package.legacy_digest)
    package["package_sha256"] = analysis_package.legacy_digest(package)
    assert analysis_package.verify_package(package) == package
    rewritten = copy.deepcopy(package)
    rewritten["result"]["summary"]["value"] = 1
    with pytest.raises(ValueError, match="checksum mismatch"):
        analysis_package.verify_package(rewritten)


@pytest.mark.parametrize("number", [float("inf"), float("nan"), 10**400])
def test_package_digest_rejects_nonfinite_binary64_numbers(number):
    with pytest.raises(ValueError, match="finite IEEE 754"):
        analysis_package.digest({"number": number})


@pytest.mark.parametrize("algorithm", ["unknown-algorithm", [], {"name": analysis_package.HASH_ALGORITHM}])
def test_unsupported_package_hash_algorithms_return_422_without_writes(algorithm):
    from fastapi import HTTPException
    package = portable_package()
    package["hash_algorithm"] = algorithm
    with pytest.raises(ValueError, match="Unsupported analysis package hash algorithm"):
        analysis_package.verify_package(package)
    with pytest.raises(HTTPException) as caught:
        api.import_package(ImportPackage(employee_id="001", package=package), session=None)
    assert caught.value.status_code == 422

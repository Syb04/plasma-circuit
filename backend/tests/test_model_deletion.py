"""Deletion must hide selected models without losing immutable research inputs."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import inspect, text

from app import database as db, worker
from test_api import client, document, run, save, solver  # noqa: F401 - shared isolated fixture
from test_workflows import create_study, succeed


def delete(client, *circuits, employee="000456"):
    return client.post("/api/circuits/delete", json={"employee_id": employee,
        "circuits": [{"id": circuit["id"], "expected_revision": circuit["revision"]} for circuit in circuits]})


def test_batch_delete_hides_only_selected_models_and_records_employee(client):
    first, second, survivor = [save(client, name=name) for name in ("削除 A", "削除 B", "残すモデル")]
    unchanged = client.get(f"/api/circuits/{survivor['id']}").json()
    response = delete(client, first, second, employee="  000456  ")
    assert response.status_code == 200, response.text
    assert response.json()["deleted_ids"] == [first["id"], second["id"]]
    assert response.json()["deleted_at"]
    for params in ({}, {"limit": 25}, {"q": "削除", "limit": 25}, {"updated_by": "000123", "sort": "name_asc"}):
        listing = client.get("/api/circuits", params=params).json()
        assert listing["total_all"] == 1
        assert {row["id"] for row in listing["circuits"]} == (set() if params.get("q") else {survivor["id"]})
        assert listing["total"] == len(listing["circuits"])
    assert client.get(f"/api/circuits/{survivor['id']}").json() == unchanged
    with db.session() as session:
        for circuit in (first, second):
            row = session.get(db.Circuit, circuit["id"])
            assert row.deleted_by == "000456" and row.deleted_at is not None
            assert row.revision == circuit["revision"] and row.updated_by == circuit["updated_by"]
            assert row.document == circuit["document"]
        assert session.query(db.CircuitRevision).count() == 3
        assert session.get(db.Circuit, survivor["id"]).deleted_at is None


@pytest.mark.parametrize("invalid", ["stale", "missing", "deleted"])
def test_stale_missing_or_deleted_target_aborts_the_whole_batch(client, invalid):
    first, second = save(client, name="保持 A"), save(client, name="保持 B")
    expected = 404 if invalid == "missing" else 409
    if invalid == "stale":
        assert client.put(f"/api/circuits/{second['id']}", json={"employee_id": "other",
            "expected_revision": 1, "document": document(name="他の人の更新")}).status_code == 200
    elif invalid == "missing":
        second = {**second, "id": "missing"}
    else:
        assert delete(client, second).status_code == 200
    response = delete(client, first, second)
    assert response.status_code == expected, response.text
    assert client.get(f"/api/circuits/{first['id']}").status_code == 200
    with db.session() as session:
        row = session.get(db.Circuit, first["id"])
        assert row.deleted_at is None and row.deleted_by is None
        if invalid == "stale":
            assert session.get(db.Circuit, second["id"]).deleted_at is None
    if invalid == "deleted":
        assert client.get(f"/api/circuits/{second['id']}").status_code == 404


@pytest.mark.parametrize("mutation", ["missing_employee", "blank_employee", "empty", "duplicates", "too_many",
    "zero_revision", "float_revision", "boolean_revision", "string_revision", "missing_revision", "unknown_field", "target_unknown_field"])
def test_invalid_deletion_never_writes(client, mutation):
    circuit = save(client)
    unchanged = client.get(f"/api/circuits/{circuit['id']}").json()
    payload = {"employee_id": "000456", "circuits": [{"id": circuit["id"], "expected_revision": 1}]}
    if mutation == "missing_employee":
        payload.pop("employee_id")
    elif mutation == "blank_employee":
        payload["employee_id"] = "  "
    elif mutation == "empty":
        payload["circuits"] = []
    elif mutation == "duplicates":
        payload["circuits"] *= 2
    elif mutation == "too_many":
        payload["circuits"] = [{"id": str(i), "expected_revision": 1} for i in range(101)]
    elif mutation == "missing_revision":
        payload["circuits"][0].pop("expected_revision")
    elif mutation.endswith("revision"):
        payload["circuits"][0]["expected_revision"] = {"zero_revision": 0, "float_revision": 1.5,
            "boolean_revision": True, "string_revision": "1"}[mutation]
    elif mutation == "unknown_field":
        payload["force"] = True
    else:
        payload["circuits"][0]["force"] = True
    assert client.post("/api/circuits/delete", json=payload).status_code == 422
    assert client.get(f"/api/circuits/{circuit['id']}").json() == unchanged
    with db.session() as session:
        assert session.get(db.Circuit, circuit["id"]).deleted_at is None


def test_deleted_model_cannot_be_opened_updated_or_used_for_new_jobs(client):
    circuit = save(client)
    assert delete(client, circuit).status_code == 200
    assert client.get(f"/api/circuits/{circuit['id']}").status_code == 404
    assert client.put(f"/api/circuits/{circuit['id']}", json={"employee_id": "000123",
        "expected_revision": 1, "document": document(name="復活させない")}).status_code == 404
    assert client.post("/api/runs", json={"employee_id": "000123", "circuit_id": circuit["id"],
        "expected_revision": 1, "analysis": {"kind": "op"}}).status_code == 404
    assert create_study(client, circuit).status_code == 404
    with db.session() as session:
        assert session.query(db.CircuitRevision).count() == 1
        assert session.query(db.SimulationRun).count() == session.query(db.Study).count() == 0
        assert session.get(db.Circuit, circuit["id"]).document == circuit["document"]
    assert not client.queue.jobs


def test_queued_job_finishes_after_deletion_and_preserves_results_exports_and_package(client, monkeypatch):
    circuit = save(client)
    created = run(client, circuit)
    before = client.get(f"/api/runs/{created['id']}").json()
    assert before["status"] == "queued"
    assert delete(client, circuit).status_code == 200
    assert client.queue.jobs[created["id"]].state == "queued"
    result = {"kind": "transient", "converged": True, "summary": {"power_w": 2.5},
        "axis": {"name": "time", "unit": "s", "values": [0., 1e-6]},
        "signals": [{"name": "V(out)", "unit": "V", "values": [1., 2.]}]}
    seen = []
    def execute(snapshot, analysis, *args, **kwargs):
        seen.append(snapshot)
        return result
    solver(monkeypatch, execute)
    worker.execute_run(created["id"])
    after = client.get(f"/api/runs/{created['id']}").json()
    assert after["status"] == "succeeded" and after["result"] == result
    assert seen == [before["snapshot"]]
    assert after["snapshot"] == before["snapshot"] and after["analysis"] == before["analysis"]
    history = client.get("/api/runs", params={"circuit_id": circuit["id"]}).json()["runs"]
    assert [row["id"] for row in history] == [created["id"]]
    assert "V(out) [V]" in client.get(f"/api/runs/{created['id']}/export.csv").text
    package = client.get(f"/api/runs/{created['id']}/package")
    assert package.status_code == 200, package.text
    assert package.json()["input"]["document"] == circuit["document"]
    assert package.json()["result"] == result
    comparison_run = run(client, save(client, name="比較先"))
    succeed(comparison_run["id"], result)
    comparison = client.post("/api/compare", json={"run_ids": [created["id"], comparison_run["id"]], "phase_align": False})
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()["runs"][0]["summary"] == result["summary"]


def test_study_history_and_retries_survive_model_deletion(client):
    circuit = save(client)
    study = create_study(client, circuit).json()
    first, second, third = study["cases"]
    succeed(first["run_id"])
    succeed(third["run_id"])
    with db.session() as session:
        failed = session.get(db.SimulationRun, second["run_id"])
        failed.status = "failed"
        failed.finished_at = db.utcnow()
        session.commit()
    assert delete(client, circuit).status_code == 200
    history = client.get(f"/api/studies/{study['id']}").json()
    assert history["snapshot"] == circuit["document"] and history["counts"]["succeeded"] == 2
    assert client.get(f"/api/studies/{study['id']}/export.csv").status_code == 200
    resumed = client.post(f"/api/studies/{study['id']}/resume", json={"employee_id": "000456"})
    assert resumed.status_code == 202, resumed.text
    cases = resumed.json()["cases"]
    assert cases[0]["run_id"] == first["run_id"] and cases[2]["run_id"] == third["run_id"]
    assert cases[1]["run_id"] != second["run_id"] and len(cases[1]["attempts"]) == 2
    with db.session() as session:
        original = session.get(db.SimulationRun, second["run_id"])
        retry = session.get(db.SimulationRun, cases[1]["run_id"])
        assert retry.snapshot == original.snapshot and retry.analysis == original.analysis
        assert retry.circuit_revision == original.circuit_revision == 1
        assert session.query(db.StudyCase).count() == 3 and session.query(db.StudyAttempt).count() == 4


def test_existing_sqlite_database_is_migrated_without_changing_saved_inputs(tmp_path):
    db.configure_database(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    snapshot = document(name="既存モデル")
    created_at = db.utcnow().isoformat()
    try:
        with db.engine.begin() as connection:
            connection.execute(text("""CREATE TABLE circuits (
                id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL, revision INTEGER NOT NULL,
                document JSON NOT NULL, created_by VARCHAR(80) NOT NULL, updated_by VARCHAR(80) NOT NULL,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"""))
            connection.execute(text("""INSERT INTO circuits VALUES
                ('legacy', '既存モデル', 1, :document, '000123', '000123', :created, :created)"""),
                {"document": json.dumps(snapshot), "created": created_at})
            db.CircuitRevision.__table__.create(connection)
            db.SimulationRun.__table__.create(connection)
            original_row = tuple(connection.execute(text("SELECT * FROM circuits")).one())
        with db.session() as session:
            session.add(db.CircuitRevision(circuit_id="legacy", revision=1, document=snapshot, employee_id="000123"))
            session.add(db.SimulationRun(id="legacy-run", circuit_id="legacy", circuit_revision=1, employee_id="000123",
                snapshot=snapshot, analysis={"kind": "op"}, runtime_config={"ngspice": "legacy"}, status="succeeded",
                result_compressed=db.compress_result({"summary": {"voltage": 5}})))
            session.commit()
        for _ in range(2):
            db.init_database()
            with db.engine.connect() as connection:
                assert tuple(connection.execute(text("SELECT * FROM circuits")).one()) == (*original_row, None, None)
                assert {column["name"] for column in inspect(connection).get_columns("circuits")} >= {"deleted_at", "deleted_by"}
            with db.session() as session:
                assert session.query(db.CircuitRevision).one().document == snapshot
                result = session.get(db.SimulationRun, "legacy-run")
                assert result.snapshot == snapshot and result.runtime_config == {"ngspice": "legacy"}
                assert db.decompress_result(result.result_compressed) == {"summary": {"voltage": 5}}
                assert session.get(db.Circuit, "legacy").deleted_at is None
    finally:
        db.engine.dispose()

"""Persistence and job-life-cycle tests independent of the numerical solver."""
from __future__ import annotations

import csv
import io
import sys
import types
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError
from rq.exceptions import NoSuchJobError

from app import api, database as db, worker


class FakeConnection:
    def ping(self):
        return True


class FakeJob:
    def __init__(self, run_id, state="queued", exc_info=None):
        self.id = run_id
        self.args = (run_id,)
        self.state = state
        self.exc_info = exc_info

    def get_status(self, refresh=True):
        return self.state

    def cancel(self):
        self.state = "canceled"


class FakeQueue:
    connection = FakeConnection()

    def __init__(self):
        self.jobs = {}

    def enqueue(self, function, run_id, **options):
        assert options["job_id"] == run_id
        assert options["job_timeout"] == 180
        assert options["on_failure"] is worker.on_failure
        assert options["on_stopped"] is worker.on_stopped
        job = FakeJob(run_id)
        self.jobs[run_id] = job
        return job


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db.configure_database(f"sqlite:///{tmp_path / 'api.sqlite'}")
    queue = FakeQueue()
    monkeypatch.setenv("JOB_TIMEOUT_SECONDS", "180")
    monkeypatch.setattr(worker, "get_queue", lambda: queue)
    monkeypatch.setattr(worker, "redis_connection", lambda: queue.connection)

    def fetch(cls, job_id, connection=None):
        if job_id not in queue.jobs:
            raise NoSuchJobError(job_id)
        return queue.jobs[job_id]

    monkeypatch.setattr(api.Job, "fetch", classmethod(fetch))
    with TestClient(api.app) as test_client:
        test_client.queue = queue
        yield test_client
    db.engine.dispose()


def document(name="回路", value=1000):
    return {
        "name": name,
        "components": [{"id": "r1", "kind": "R", "ports": ["p", "n"], "parameters": {"value": value}}],
        "models": [{"name": "CUSTOM", "definition": ".model CUSTOM D(Is=1e-14)"}],
    }


def save(client, **kwargs):
    response = client.post("/api/circuits", json={"employee_id": "000123", "document": document(**kwargs)})
    assert response.status_code == 201, response.text
    return response.json()


def run(client, circuit):
    response = client.post("/api/runs", json={"employee_id": "000123", "circuit_id": circuit["id"], "expected_revision": circuit["revision"], "analysis": {"kind": "op"}})
    assert response.status_code == 202, response.text
    return response.json()


def solver(monkeypatch, function):
    monkeypatch.setitem(sys.modules, "app.engine", types.SimpleNamespace(execute_circuit=function))


def test_employee_required_and_leading_zero_preserved(client):
    assert client.post("/api/circuits", json={"document": document()}).status_code == 422
    assert client.post("/api/circuits", json={"employee_id": "  ", "document": document()}).status_code == 422
    saved = save(client)
    assert saved["created_by"] == saved["updated_by"] == "000123"
    assert client.get("/api/circuits").json()["circuits"][0]["id"] == saved["id"]


def test_model_list_paginates_with_stable_order_and_lightweight_metadata(client):
    now = db.utcnow()
    with db.session() as session:
        session.add_all(db.Circuit(id=f"model-{i:03}", name="同名モデル", revision=1,
            document={"description": f"メモ {i}", "components": [{"large": "x" * 2000}]},
            created_by="000123", updated_by="000456", created_at=now, updated_at=now)
            for i in range(53))
        session.commit()
    pages = [client.get("/api/circuits", params={"limit": 25, "offset": offset}).json()
             for offset in (0, 25, 50)]
    assert [len(page["circuits"]) for page in pages] == [25, 25, 3]
    assert all(page["total"] == page["total_all"] == 53 for page in pages)
    assert [row["id"] for page in pages for row in page["circuits"]] == [f"model-{i:03}" for i in range(53)]
    assert pages[0]["circuits"][0]["description"] == "メモ 0"
    assert all("document" not in row for page in pages for row in page["circuits"])
    assert len(client.get("/api/circuits").json()["circuits"]) == 53  # Existing clients remain compatible.
    past_end = client.get("/api/circuits", params={"limit": 25, "offset": 100}).json()
    assert past_end["total"] == 53 and past_end["circuits"] == []


@pytest.fixture()
def searchable_models(client):
    now = db.utcnow()
    rows = [("alpha", "Ar CCP", "シリコン表面", "000123", "000456", 1),
            ("beta", "O2 model 100%", "Pulse response", "000456", "000123", 10),
            ("gamma", "Legacy_model", "old", "00123", "00123", 60)]
    with db.session() as session:
        session.add_all(db.Circuit(id=id, name=name, document={"description": memo},
            created_by=creator, updated_by=updater, created_at=now - timedelta(days=days + 1),
            updated_at=now - timedelta(days=days)) for id, name, memo, creator, updater, days in rows)
        session.commit()
    return rows


def test_model_list_searches_name_memo_and_employees_with_literal_wildcards(client, searchable_models):
    def ids(query):
        result = client.get("/api/circuits", params={"q": query, "limit": 25})
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["total_all"] == 3
        assert payload["total"] == len(payload["circuits"])
        return {row["id"] for row in payload["circuits"]}
    assert ids("  ar ccp  ") == {"alpha"}
    assert ids("シリコン") == {"alpha"}
    assert ids("pUlSe") == {"beta"}
    assert ids("000123") == {"alpha", "beta"}
    assert ids("%") == {"beta"}
    assert ids("_") == {"gamma"}
    assert ids("no matches") == set()
    assert ids("' OR 1=1 --") == set()


def test_model_list_combines_exact_employee_period_and_sort_filters(client, searchable_models):
    def list_models(**params):
        response = client.get("/api/circuits", params={"limit": 25, **params})
        assert response.status_code == 200, response.text
        return response.json()
    assert [row["id"] for row in list_models(updated_by=" 000123 ")["circuits"]] == ["beta"]
    assert [row["id"] for row in list_models(updated_by="00123")["circuits"]] == ["gamma"]
    assert list_models(q="O2", updated_by="000123", updated_within_days=7)["total"] == 0
    assert list_models(q="O2", updated_by="000123", updated_within_days=30)["total"] == 1
    assert [row["id"] for row in list_models(updated_within_days=7)["circuits"]] == ["alpha"]
    assert [row["id"] for row in list_models(sort="updated_asc")["circuits"]] == ["gamma", "beta", "alpha"]
    assert [row["id"] for row in list_models(sort="name_asc")["circuits"]] == ["alpha", "gamma", "beta"]
    assert [row["id"] for row in list_models(sort="name_desc")["circuits"]] == ["beta", "gamma", "alpha"]
    assert [row["id"] for row in list_models(sort="created_desc")["circuits"]] == ["alpha", "beta", "gamma"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1},
    {"sort": "unknown"}, {"updated_within_days": 0}, {"updated_within_days": 3651},
    {"q": "x" * 201}, {"updated_by": "x" * 81}])
def test_model_list_validates_pagination_and_filters(client, params):
    assert client.get("/api/circuits", params=params).status_code == 422


def test_revision_conflict_and_immutable_run_snapshot(client):
    saved = save(client)
    created = run(client, saved)
    response = client.put(f"/api/circuits/{saved['id']}", json={"employee_id": "000456", "expected_revision": 1, "document": document(value=2200)})
    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert response.json()["updated_by"] == "000456"
    stale = client.put(f"/api/circuits/{saved['id']}", json={"employee_id": "000123", "expected_revision": 1, "document": document(value=999)})
    assert stale.status_code == 409
    old_run = client.get(f"/api/runs/{created['id']}").json()
    assert old_run["snapshot"]["components"][0]["parameters"]["value"] == 1000
    assert old_run["snapshot"]["models"] == document()["models"]
    assert old_run["circuit_revision"] == 1
    assert old_run["runtime_config"]["job_timeout_seconds"] == 180
    with db.session() as session:
        assert session.query(db.CircuitRevision).count() == 2
    assert client.post("/api/runs", json={"employee_id": "000123", "circuit_id": saved["id"], "expected_revision": 1, "analysis": {"kind": "op"}}).status_code == 409


def test_source_exponents_and_detailed_diode_roundtrip_into_immutable_snapshot(client):
    original = {"name": "指数・詳細ダイオード", "components": [
        {"id": "v1", "kind": "V", "ports": ["p", "n"], "parameters": {"dc": 0,
            "waveform": {"kind": "pulse", "rise": 1e-9, "fall": 2e-9, "width": 5e-7, "period": 1e-6}}},
        {"id": "d1", "kind": "D", "ports": ["p", "n"], "parameters": {"area": 2,
            "model_parameters": {"IS": 1e-12, "N": 1.5, "BV": 75, "IBV": 1e-3, "CJO": 1e-11, "TT": 1e-8}}},
    ]}
    response = client.post("/api/circuits", json={"employee_id": "000123", "document": original})
    assert response.status_code == 201, response.text
    saved = response.json()
    restored = client.get(f"/api/circuits/{saved['id']}").json()["document"]
    assert [c["parameters"] for c in restored["components"]] == [c["parameters"] for c in original["components"]]
    created = run(client, saved)
    del restored["components"][1]["parameters"]["model_parameters"]["BV"]
    restored["components"][0]["parameters"]["waveform"]["rise"] = 3e-9
    updated = client.put(f"/api/circuits/{saved['id']}", json={"employee_id": "000123", "expected_revision": 1, "document": restored})
    assert updated.status_code == 200, updated.text
    snapshot = client.get(f"/api/runs/{created['id']}").json()["snapshot"]
    assert snapshot["components"] == saved["document"]["components"]
    assert "BV" not in updated.json()["document"]["components"][1]["parameters"]["model_parameters"]


def test_queue_unavailable_leaves_failed_durable_history(client, monkeypatch):
    saved = save(client)

    def unavailable(*args, **kwargs):
        raise ConnectionError("not connected")

    monkeypatch.setattr(worker, "enqueue_run", unavailable)
    response = client.post("/api/runs", json={"employee_id": "000123", "circuit_id": saved["id"], "expected_revision": 1, "analysis": {"kind": "op"}})
    assert response.status_code == 503
    result = client.get(f"/api/runs/{response.json()['detail']['run_id']}").json()
    assert result["status"] == "failed"
    assert result["snapshot"]["name"] == saved["name"]
    assert result["finished_at"] is not None


def test_separate_worker_persists_compressed_result_and_csv_samples(client, monkeypatch):
    created = run(client, save(client))
    samples = [0.0, 0.123456789, 0.25]
    result = {"kind": "transient", "converged": True, "summary": {}, "axis": {"name": "time", "unit": "s", "values": samples}, "signals": [{"name": "V(out)", "unit": "V", "values": [1.0, 2.0, 3.0]}], "solver": {"ngspice": "test"}}
    solver(monkeypatch, lambda *args: result)
    worker.execute_run(created["id"])
    persisted = client.get(f"/api/runs/{created['id']}").json()
    assert persisted["status"] == "succeeded"
    assert persisted["result"] == result
    with db.session() as session:
        record = session.get(db.SimulationRun, created["id"])
        assert isinstance(record.result_compressed, bytes)
        assert record.started_at is not None
    csv_response = client.get(f"/api/runs/{created['id']}/export.csv")
    rows = list(csv.reader(io.StringIO(csv_response.text.lstrip("\ufeff"))))
    assert csv_response.status_code == 200
    assert rows[0] == ["time [s]", "V(out) [V]"]
    assert [float(row[0]) for row in rows[1:]] == samples


def test_op_export_includes_numerical_table_dict_rows(client, monkeypatch):
    created = run(client, save(client))
    solver(monkeypatch, lambda *args: {"kind": "op", "summary": {}, "tables": [{"name": "動作点", "columns": ["signal", "value", "unit"], "rows": [{"signal": "V(out)", "value": 5, "unit": "V"}]}]})
    worker.execute_run(created["id"])
    response = client.get(f"/api/runs/{created['id']}/export.csv")
    assert "V(out),5,V" in response.text


def test_cancel_queued_job_prevents_solver_execution(client, monkeypatch):
    created = run(client, save(client))
    canceled = client.post(f"/api/runs/{created['id']}/cancel").json()
    assert canceled["status"] == "canceled"
    assert canceled["cancel_requested"] is True
    assert client.queue.jobs[created["id"]].state == "canceled"
    solver(monkeypatch, lambda *args: pytest.fail("canceled job must not execute"))
    worker.execute_run(created["id"])
    assert client.get(f"/api/runs/{created['id']}").json()["result"] is None


def test_cancel_running_requests_stop_and_callback_persists(client, monkeypatch):
    created = run(client, save(client))
    with db.session() as session:
        record = session.get(db.SimulationRun, created["id"])
        record.status = "running"
        record.started_at = db.utcnow()
        session.commit()
    client.queue.jobs[created["id"]].state = "started"
    stopped = []
    monkeypatch.setattr(api, "send_stop_job_command", lambda connection, job_id: stopped.append(job_id))
    response = client.post(f"/api/runs/{created['id']}/cancel").json()
    assert stopped == [created["id"]]
    assert response["status"] == "canceled"
    worker.on_stopped(client.queue.jobs[created["id"]], client.queue.connection)
    assert client.get(f"/api/runs/{created['id']}").json()["status"] == "canceled"


def test_timeout_and_crash_callbacks_repair_database(client):
    created = run(client, save(client))
    JobTimeoutException = type("JobTimeoutException", (Exception,), {})
    worker.on_failure(client.queue.jobs[created["id"]], client.queue.connection, JobTimeoutException, JobTimeoutException("timeout"), None)
    assert client.get(f"/api/runs/{created['id']}").json()["status"] == "timed_out"
    crashed = run(client, save(client, name="crash"))
    worker.on_work_horse_killed(client.queue.jobs[crashed["id"]], 1, 137, None)
    assert client.get(f"/api/runs/{crashed['id']}").json()["status"] == "failed"


def test_lost_redis_job_and_failed_queue_state_are_recovered(client):
    created = run(client, save(client))
    client.queue.jobs[created["id"]].state = "failed"
    client.queue.jobs[created["id"]].exc_info = "JobTimeoutException: exhausted"
    assert client.get(f"/api/runs/{created['id']}").json()["status"] == "timed_out"
    lost = run(client, save(client, name="lost"))
    del client.queue.jobs[lost["id"]]
    with db.session() as session:
        record = session.get(db.SimulationRun, lost["id"])
        record.created_at = db.utcnow() - timedelta(minutes=2)
        session.commit()
    worker.recover_active_runs()
    assert client.get(f"/api/runs/{lost['id']}").json()["status"] == "failed"


def test_invalid_result_fails_without_manufactured_success(client, monkeypatch):
    created = run(client, save(client))
    solver(monkeypatch, lambda *args: {"summary": {"value": float("nan")}})
    with pytest.raises(ValueError):
        worker.execute_run(created["id"])
    result = client.get(f"/api/runs/{created['id']}").json()
    assert result["status"] == "failed"
    assert result["result"] is None
    assert client.get(f"/api/runs/{created['id']}/export.csv").status_code == 409

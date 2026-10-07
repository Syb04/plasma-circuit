#!/usr/bin/env python3
"""Deployed cancellation/idle checks; optional isolated real-solver timeout check.

HTTP mode: python scripts/check_jobs.py --base-url http://localhost:8080
Timeout mode: run this file via stdin inside the API container with
    python - --isolated-timeout
It creates a separate Redis queue and a short-lived worker without changing the
application worker's configuration. Test circuits/runs remain in the audit DB.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid


def http_checks(base: str) -> None:
    def request(path, data=None):
        payload = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(base.rstrip("/") + path, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    def start(preset, analysis=None):
        document = copy.deepcopy(preset["document"])
        document["name"] = "ジョブ制御検証: " + document["name"]
        saved = request("/api/circuits", {"employee_id": "000JOBTEST", "document": document})
        return request("/api/runs", {"employee_id": "000JOBTEST", "circuit_id": saved["id"], "expected_revision": saved["revision"], "analysis": analysis or preset["analysis"]})

    def wait(run_id, target, deadline_seconds=30):
        deadline = time.monotonic() + deadline_seconds
        last = None
        while time.monotonic() < deadline:
            last = request("/api/runs/" + run_id)
            if last["status"] in target:
                return last
            if last["status"] in {"succeeded", "failed", "canceled", "timed_out"}:
                raise AssertionError(last)
            time.sleep(0.1)
        raise AssertionError(f"Job did not reach {target}: {last}")

    presets = {item["id"]: item for item in request("/api/presets")["presets"]}
    slow = copy.deepcopy(presets["ccp-ar"]["analysis"])
    slow["kind"] = "global"
    slow["settings"].update({"cycles": 120, "points_per_cycle": 512, "max_global_iterations": 40})
    active = start(presets["ccp-ar"], slow)
    wait(active["id"], {"running"})
    queued = start(presets["divider"])
    assert queued["status"] == "queued", queued
    response = request("/api/runs/" + queued["id"] + "/cancel", {})
    assert response["status"] == "canceled" and response["cancel_requested"]
    response = request("/api/runs/" + active["id"] + "/cancel", {})
    assert response["status"] == "canceled" and response["cancel_requested"]
    wait(active["id"], {"canceled"})
    wait(queued["id"], {"canceled"})
    print("PASS: real RQ queued cancellation and running ngspice/plasma cancellation", flush=True)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        time.sleep(1)
    completed = wait(start(presets["divider"])["id"], {"succeeded"})
    assert completed["result"]["solver"]["pyspice"] == "1.5"
    print("PASS: worker accepts a real ngspice job after 12 seconds idle", flush=True)


def isolated_timeout() -> None:
    from rq import Queue
    from app import database as db, worker
    from app.presets import get_presets

    os.environ["JOB_TIMEOUT_SECONDS"] = "10"
    db.init_database()
    preset = next(item for item in get_presets()["presets"] if item["id"] == "ccp-ar")
    analysis = copy.deepcopy(preset["analysis"])
    analysis["kind"] = "global"
    analysis["settings"].update({"cycles": 120, "points_per_cycle": 512, "max_global_iterations": 40})
    with db.session() as session:
        document = db.json_copy(preset["document"])
        document["name"] = "独立キューtimeout検証"
        circuit = db.Circuit(name=document["name"], document=document, created_by="000JOBTEST", updated_by="000JOBTEST")
        session.add(circuit)
        session.flush()
        session.add(db.CircuitRevision(circuit_id=circuit.id, revision=1, document=document, employee_id="000JOBTEST"))
        run = db.SimulationRun(circuit_id=circuit.id, circuit_revision=1, employee_id="000JOBTEST", snapshot=document, analysis=analysis, runtime_config=worker.runtime_config())
        session.add(run)
        session.commit()
        run_id = run.id
    queue_name = "job-control-test-" + uuid.uuid4().hex
    queue = Queue(queue_name, connection=worker.redis_connection(), default_timeout=10)
    worker.enqueue_run(run_id, queue)
    code = "from app import database as d, worker as w; from rq import Queue, Worker; d.init_database(); c=w.redis_connection(blocking=True); q=Queue(%r, connection=c); Worker([q], connection=c, work_horse_killed_handler=w.on_work_horse_killed).work(burst=True)" % queue_name
    subprocess.run([sys.executable, "-c", code], check=True, timeout=40)
    with db.session() as session:
        run = session.get(db.SimulationRun, run_id)
        assert run.status == "timed_out", (run.status, run.error)
        assert run.finished_at is not None and run.result_compressed is None
    print("PASS: real global-model/RQ 10-second timeout commits timed_out to PostgreSQL", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--isolated-timeout", action="store_true")
    args = parser.parse_args()
    isolated_timeout() if args.isolated_timeout else http_checks(args.base_url)

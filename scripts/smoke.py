#!/usr/bin/env python3
"""Exercise the deployed API, PostgreSQL revisions, RQ worker and real solver."""
from __future__ import annotations

import argparse
import copy
import json
import math
import time
import urllib.error
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--include-plasma", action="store_true")
    parser.add_argument("--include-global", action="store_true")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    def request(path: str, payload: dict | None = None, method: str | None = None, expected: int = 200):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                assert response.status == expected, (path, response.status)
                content = response.read()
                return json.loads(content) if response.headers.get_content_type() == "application/json" else content.decode()
        except urllib.error.HTTPError as error:
            if error.code == expected:
                return json.loads(error.read())
            raise RuntimeError(f"{path}: HTTP {error.code}: {error.read().decode()}") from error

    def wait_run(run_id: str):
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            run = request("/api/runs/" + run_id)
            if run["status"] in {"succeeded", "failed", "canceled", "timed_out"}:
                assert run["status"] == "succeeded", run.get("error")
                return run
            time.sleep(0.5)
        raise AssertionError("worker did not complete within the smoke-test deadline")

    health = request("/api/health")
    assert health["status"] == "ok", health
    presets = {p["id"]: p for p in request("/api/presets")["presets"]}
    kinds = {c["kind"] for c in request("/api/catalog")["components"]}
    assert {"R", "C", "L", "EDD", "D", "Q", "E", "F", "G", "H", "K", "S", "W", "T", "O", "U", "Y", "P", "X"}.issubset(kinds)

    first = copy.deepcopy(presets["divider"]["document"])
    first["name"] = "API検証：抵抗分圧"
    request("/api/circuits", {"employee_id": "  ", "document": first}, expected=422)
    saved = request("/api/circuits", {"employee_id": "000001", "document": first}, expected=201)
    assert saved["created_by"] == "000001" and saved["revision"] == 1
    queued = request("/api/runs", {"employee_id": "000001", "circuit_id": saved["id"], "expected_revision": 1, "analysis": presets["divider"]["analysis"]}, expected=202)

    changed = copy.deepcopy(first)
    next(c for c in changed["components"] if c["id"] == "r1")["parameters"]["value"] = 5000
    updated = request("/api/circuits/" + saved["id"], {"employee_id": "000002", "expected_revision": 1, "document": changed}, method="PUT")
    assert updated["revision"] == 2
    request("/api/circuits/" + saved["id"], {"employee_id": "000003", "expected_revision": 1, "document": first}, method="PUT", expected=409)
    run = wait_run(queued["id"])
    assert run["circuit_revision"] == 1
    assert next(c for c in run["snapshot"]["components"] if c["id"] == "r1")["parameters"]["value"] == 1000
    values = [value for value in run["result"]["summary"].values() if isinstance(value, (int, float))]
    assert any(math.isclose(value, 10 / 3, rel_tol=1e-6) for value in values), values
    assert request("/api/runs/" + queued["id"] + "/export.csv")
    print("PASS: required employee ID, leading zeros, PostgreSQL save, revision conflict, immutable snapshot, RQ/ngspice divider, CSV", flush=True)

    selected = ["rc", "edd"]
    if args.include_plasma:
        selected += ["ccp-ar", "ccp-o2", "ccp-cf4"]
    if args.include_global:
        selected += ["global-ar"]
    for preset_id in selected:
        if preset_id == "global-ar":
            preset = copy.deepcopy(presets["ccp-ar"])
            preset["analysis"]["kind"] = "global"
        else:
            preset = presets[preset_id]
        doc = copy.deepcopy(preset["document"])
        doc["name"] = "API検証：" + doc["name"]
        saved = request("/api/circuits", {"employee_id": "000001", "document": doc}, expected=201)
        queued = request("/api/runs", {"employee_id": "000001", "circuit_id": saved["id"], "expected_revision": 1, "analysis": preset["analysis"]}, expected=202)
        run = wait_run(queued["id"])
        result = run["result"]
        assert result["solver"]["pyspice"] == "1.5"
        assert result["signals"] and result["axis"]["values"]
        assert all(math.isfinite(value) for signal in result["signals"] for value in signal["values"])
        assert result["converged"], result.get("diagnostics")
        if result["kind"] in {"ccp", "global"}:
            diagnostics = result["diagnostics"]
            assert abs(diagnostics["mean_electrode_current_a"]) < diagnostics["dc_current_tolerance_a"]
            assert diagnostics["periodic_current_relative_error"] < 0.02
        if result["kind"] == "global":
            balance = result["diagnostics"]["global_balances"]
            assert abs(balance["energy_residual_w"]) / balance["electron_energy_loss_w"] < 0.01
        print(f"PASS: deployed {preset_id}, {len(result['axis']['values'])} saved samples", flush=True)


if __name__ == "__main__":
    main()

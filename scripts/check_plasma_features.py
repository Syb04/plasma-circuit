#!/usr/bin/env python3
"""Real queued Docker API checks; creates named validation circuits and runs."""
import argparse
import copy
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--output", default="reports/simulator-extensions/api-validation.json")
    args = parser.parse_args()
    report = {"passed": False, "base_url": args.base_url, "employee_id": "feature-validation", "cases": []}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    def checkpoint():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    def request(route, payload=None):
        data = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        req = urllib.request.Request(args.base_url.rstrip("/")+"/api"+route, data=data,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode()}") from exc
    presets = {p["id"]: p for p in request("/presets")["presets"]}
    tests = [
        ("ccp_iedf_refinement", "ccp-ar", "ccp", {"cycles": 24, "points_per_cycle": 256,
         "iedf": {"enabled": True, "particles_per_species": 128, "steps_per_rf_period": 128},
         "numerical_validation": {"enabled": True, "refine_cycles": False, "relative_tolerance": .05}}),
        ("external_rf", "ccp-external-rf", "ccp", {"cycles": 32, "points_per_cycle": 256}),
        ("oxygen_absorbed", "ccp-o2", "global", {"power_mode": "prescribed_absorbed",
         "electron_heating_model": "prescribed_power", "absorbed_power_w": 500,
         "gas_temperature_k": 600, "cathode_diameter_m": .304, "gap_m": .076,
         "wall_edge_factor": .2, "axial_edge_factor": .2, "radial_edge_factor": .2}),
        ("argon_pulse_gas_heat", "ccp-ar", "global_transient", {"power_mode": "prescribed_absorbed",
         "absorbed_power_w": 500, "stop_time_s": 1e-5, "pulse_frequency_hz": 1e5,
         "pulse_duty_cycle": .5, "pulse_off_fraction": .5}),
        ("radial_refinement", "ccp-ar", "radial", {"rf_peak_voltage": 5, "radial_cells": 12,
         "radial_feed_radius_m": .01, "numerical_validation": {"enabled": True, "relative_tolerance": .1}}),
    ]
    try:
        for name, preset_name, kind, overrides in tests:
            print(f"START {name}", flush=True)
            preset = copy.deepcopy(presets[preset_name])
            preset["document"]["name"] = f"Feature validation: {name} {int(time.time())}"
            saved = request("/circuits", {"employee_id": "feature-validation", "document": preset["document"]})
            analysis = {"kind": kind, "settings": {**preset["analysis"]["settings"], **overrides}}
            run = request("/runs", {"employee_id": "feature-validation", "circuit_id": saved["id"],
                "expected_revision": saved["revision"], "analysis": analysis})
            deadline = time.monotonic()+120
            while run["status"] in {"queued", "running"}:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{name}: job did not finish within 120s")
                time.sleep(.25)
                run = request(f"/runs/{run['id']}")
            if run["status"] != "succeeded":
                raise AssertionError(f"{name}: {run['status']} {run.get('error')}")
            result = run["result"]
            case = {"name": name, "circuit_id": saved["id"], "run_id": run["id"],
                "status": run["status"], "converged": result["converged"], "summary": result["summary"],
                "diagnostics": result.get("diagnostics", {}), "rf_diagnostics": result.get("rf_diagnostics"),
                "solver": result.get("solver"), "signals": [s["name"] for s in result.get("signals", [])]}
            report["cases"].append(case)
            checkpoint()
            assert result["converged"], f"{name}: numerical convergence criteria failed"
            if name == "ccp_iedf_refinement":
                assert result["iedf"]["converged"]
                assert result["diagnostics"]["numerical_validation"]["passed"]
            if name == "external_rf":
                assert any(p["name"] == "source" for p in result["rf_diagnostics"]["measurement_planes"])
            if name == "oxygen_absorbed":
                assert result["summary"]["total_plasma_absorbed_power_w"] == 500
                assert not result.get("signals"), "Prescribed power must not invent RF waveforms"
            if name == "argon_pulse_gas_heat":
                assert abs(result["diagnostics"]["total_energy_residual_j"]) < 1e-10
            print(f"PASS {name}: {run['id']}", flush=True)
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
        checkpoint()
        raise
    checkpoint()
    print(f"PASS all {len(tests)} real queued plasma cases; {path}", flush=True)


if __name__ == "__main__":
    main()

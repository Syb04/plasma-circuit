#!/usr/bin/env python3
"""Run reproducible O2 loss sensitivity cases; never calibrate chemistry rates."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.oxygen import load_oxygen_data
from app.oxygen_study import StudyInputs, solve_oxygen_case


def planned_cases() -> list[dict]:
    baseline = StudyInputs(
        absorbed_power_w=500, pressure_pa=1.3332236842105263,
        gas_temperature_k=600, radius_m=.152, length_m=.076, flow_sccm=50,
        axial_edge_factor=.2, radial_edge_factor=.2,
        diffusion_o_m2_s=1.2, diffusion_o2_m2_s=.84,
        gamma_o=.17, gamma_meta=.007,
        electron_momentum_nu_s={"O": 1e7, "O2": 1e7})
    rows: list[dict] = []

    def add(case_id: str, family: str, inputs: StudyInputs, scale: float = 1) -> None:
        rows.append({"case_id": case_id, "family": family,
                     "diffusion_scale": scale, "inputs": asdict(inputs)})

    add("baseline", "baseline", baseline)
    axes = {"hL": [.05, .1, .2, .4], "diffusion_scale": [.5, 1, 2],
            "gamma_o": [.05, .15, .17, .19, .5],
            "gamma_meta": [.001, .007, .03], "nu_s": [0, 1e7, 1e8]}
    centers = {"hL": .2, "diffusion_scale": 1, "gamma_o": .17,
               "gamma_meta": .007, "nu_s": 1e7}
    for axis, values in axes.items():
        for value in values:
            if value == centers[axis]:
                continue
            updates = {"hL": {"axial_edge_factor": value, "radial_edge_factor": value},
                       "diffusion_scale": {"diffusion_o_m2_s": 1.2*value, "diffusion_o2_m2_s": .84*value},
                       "gamma_o": {"gamma_o": value}, "gamma_meta": {"gamma_meta": value},
                       "nu_s": {"electron_momentum_nu_s": {"O": value, "O2": value}}}[axis]
            add(f"oat-{axis}-{value:g}", axis, replace(baseline, **updates),
                value if axis == "diffusion_scale" else 1)
    for h in axes["hL"]:
        for scale in axes["diffusion_scale"]:
            for gamma in axes["gamma_meta"]:
                for nu in axes["nu_s"]:
                    inputs = replace(baseline, axial_edge_factor=h, radial_edge_factor=h,
                                     diffusion_o_m2_s=1.2*scale, diffusion_o2_m2_s=.84*scale,
                                     gamma_meta=gamma, electron_momentum_nu_s={"O": nu, "O2": nu})
                    add(f"cross-h{h:g}-D{scale:g}-g{gamma:g}-nu{nu:g}", "cross", inputs, scale)
    for power in (100, 1500):
        add(f"power-{power}", "power", replace(baseline, absorbed_power_w=power))
    add("pressure-1mtorr", "pressure", replace(baseline,
        pressure_pa=1.3332236842105263/10, diffusion_o_m2_s=8.8, diffusion_o2_m2_s=6.3))
    # Apply the same loss assumptions to other source cases; do not choose a
    # different h to improve every reference point separately.
    for h in axes["hL"]:
        add(f"power-check-100-h{h:g}", "power_check", replace(baseline,
            absorbed_power_w=100, axial_edge_factor=h, radial_edge_factor=h))
        add(f"pressure-check-1-h{h:g}", "pressure_check", replace(baseline,
            pressure_pa=1.3332236842105263/10, axial_edge_factor=h, radial_edge_factor=h,
            diffusion_o_m2_s=8.8, diffusion_o2_m2_s=6.3))
    return rows


def input_key(inputs: dict) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, allow_nan=False).encode()).hexdigest()


def evaluate(inputs: dict, max_nfev: int, multistarts: int) -> dict:
    try:
        return solve_oxygen_case(inputs, max_nfev=max_nfev, multistarts=multistarts)
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def compact_row(case: dict, result: dict, references: list[dict]) -> dict:
    """The result adapter is intentionally explicit about unavailable metrics."""
    p = case["inputs"]
    losses = result.get("loss_w", {})
    densities = result.get("densities_m3", {})
    residuals = result.get("residuals", {})
    row = {
        "case_id": case["case_id"], "family": case["family"],
        "converged": int(bool(result.get("success", False))),
        "source_domain_valid": int(bool(result.get("source_domain_valid", False))),
        "pressure_mtorr": p["pressure_pa"]/0.13332236842105263,
        "power_w": p["absorbed_power_w"], "hL": p["axial_edge_factor"],
        "hR": p["radial_edge_factor"], "diffusion_scale": case["diffusion_scale"],
        "gamma_o": p["gamma_o"], "gamma_meta": p["gamma_meta"],
        "nu_s": p["electron_momentum_nu_s"]["O2"],
        "ne_m3": result.get("electron_density_m3"),
        "te_ev": result.get("temperature_ev"),
        "alpha": result.get("electronegativity"),
        "n_O2plus_m3": densities.get("O2+"), "n_O_m3": densities.get("O"),
        "loss_total_w": losses.get("total"),
        "loss_ionization_w": losses.get("ionization"),
        "loss_excitation_w": losses.get("excitation"),
        "loss_elastic_w": losses.get("elastic"),
        "loss_electron_wall_w": losses.get("electron_wall"),
        "loss_ion_wall_w": losses.get("ion_wall"),
        "energy_residual_rel": residuals.get("energy_relative"),
        "particle_residual_rel": max(map(abs, residuals.get("species_relative", {}).values()), default=None),
        "atoms_residual_rel": residuals.get("oxygen_atoms_relative"),
        "charge_residual_rel": residuals.get("charge_relative"),
        "pressure_residual_rel": residuals.get("neutral_pressure_relative"),
        "nfev": result.get("optimizer", {}).get("total_nfev"),
        "error": result.get("error", ",".join(result.get("failure_reasons", []))),
        "reference_o2plus_density_m3": None, "reference_deviation_rel": None,
    }
    for point in references:
        if (math.isclose(point["pressure_mtorr"], row["pressure_mtorr"], rel_tol=1e-8)
                and point["power_w"] == row["power_w"]):
            row["reference_o2plus_density_m3"] = point["value"]
            if row["converged"] and row["n_O2plus_m3"] is not None:
                row["reference_deviation_rel"] = row["n_O2plus_m3"]/point["value"] - 1
            break
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/oxygen-parameter-study")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-nfev", type=int, default=2000)
    parser.add_argument("--multistarts", type=int, default=3)
    parser.add_argument("--max-cases", type=int, help="Run the first N planned rows for a smoke check")
    args = parser.parse_args()
    if args.workers < 1 or args.multistarts < 1 or args.max_nfev < 1:
        parser.error("workers, multistarts and max-nfev must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    cases = planned_cases()
    if args.max_cases is not None:
        if args.max_cases < 1:
            parser.error("max-cases must be positive")
        cases = cases[:args.max_cases]
    source = load_oxygen_data()
    dataset_bytes = (ROOT / "backend/app/data/oxygen-gudmundsson-2001.json").read_bytes()
    unique = {input_key(case["inputs"]): case["inputs"] for case in cases}
    metadata = {
        "model_status": "exploratory_assumed_closure_not_validated", "case_rows": len(cases),
        "unique_inputs": len(unique), "gas": "pure O2", "source": source["reference"],
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "solver_sha256": hashlib.sha256((ROOT / "backend/app/oxygen_study.py").read_bytes()).hexdigest(),
        "study_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "power_definition": "Total prescribed absorbed power, including ion-wall energy; not generator RF power",
        "frequency_note": "This prescribed-power chemistry study does not solve frequency-dependent CCP heating",
        "energy_scope": "published_eq3_ground_neutral_approximation_not_all_48_reaction_energy_complete",
        "individual_rate_domain": "k20 from paper section3.4 p1107 requires 1<Te<4.5eV; bound-pinned points fail",
        "assumptions": {
            "edge_factors": "hL=hR=.05,.1,.2,.4: exploratory inputs, not density-dependent electronegative transport",
            "diffusion": "10 mTorr reference outputs scaled .5,1,2; 1 mTorr uses its own reference outputs",
            "gamma_o": ".15-.19 is published .17±.02; .05 and .5 are wider hypotheses, not Si measurements",
            "gamma_meta": ".007 originates from Fe; .001 and .03 are sensitivity assumptions",
            "momentum_collision_frequency": "Each target O and O2 has 0,1e7,1e8 s^-1 as an assumption; total frequency is twice this value. Zero is an omitted-elastic limiting case",
            "surface_temperature": "Not specified; gas 600 K is not wall temperature",
            "flow": "50 sccm at STP273.15K/101325Pa; neutral-only pumping closure",
        }, "references": source["comparison_points"],
        "reference_role": "Deviation is descriptive, not a pass criterion or proof of validation",
        "source_corrections": source["source_corrections"],
        "omitted_reactions": source["omitted_reactions"],
        "execution": {"workers": args.workers, "max_nfev": args.max_nfev, "multistarts": args.multistarts},
    }
    (args.output / "study-plan.json").write_text(json.dumps({"metadata": metadata, "cases": cases}, indent=2, ensure_ascii=False))
    results = {}
    # Numerical libraries must not oversubscribe the study's worker budget.
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        pending = {pool.submit(evaluate, inputs, args.max_nfev, args.multistarts): key
                   for key, inputs in unique.items()}
        for future in as_completed(pending):
            key = pending[future]
            results[key] = future.result()
            print(f"Completed {len(results)}/{len(unique)}; converged={sum(bool(r.get('success')) for r in results.values())}", flush=True)
            (args.output / "checkpoint.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, allow_nan=False))
    records = [{**case, "input_sha256": input_key(case["inputs"]),
                "result": results[input_key(case["inputs"])]} for case in cases]
    rows = [compact_row(case, results[input_key(case["inputs"])], source["comparison_points"]) for case in cases]
    metadata.update({"elapsed_seconds": time.monotonic()-started,
                     "unique_converged": sum(bool(r.get("success")) for r in results.values()),
                     "row_converged": sum(row["converged"] for row in rows)})
    (args.output / "results.json").write_text(json.dumps({"metadata": metadata, "cases": records}, indent=2, ensure_ascii=False, allow_nan=False))
    with (args.output / "cases.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"output": str(args.output), "rows": len(rows), "unique": len(unique),
                      "converged": metadata["unique_converged"], "seconds": metadata["elapsed_seconds"]}))


if __name__ == "__main__":
    main()

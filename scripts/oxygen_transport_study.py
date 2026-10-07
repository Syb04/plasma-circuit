#!/usr/bin/env python3
"""Compare an explicit ion edge factor with the published 2000 approximation.

Chemistry and energy approximations are held fixed. No measured density is used
to choose transport inputs, and the 2001 core/edge model is not reconstructed.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.oxygen import load_oxygen_data
from app.oxygen_study import StudyInputs
from oxygen_parameter_study import compact_row, evaluate, input_key


def planned_cases() -> list[dict]:
    baseline = StudyInputs(
        absorbed_power_w=500, pressure_pa=10*.13332236842105263,
        axial_edge_factor=None, radial_edge_factor=None,
        transport_mode="gudmundsson_2000",
        diffusion_o_m2_s=1.2, diffusion_o2_m2_s=.84,
        electron_momentum_nu_s={"O": 1e7, "O2": 1e7})
    rows: list[dict] = []

    def add(case_id: str, family: str, inputs: StudyInputs,
            diffusion_scale: float = 1, cross_section_scale: float = 1) -> None:
        rows.append({"case_id": case_id, "family": family,
                     "diffusion_scale": diffusion_scale,
                     "cross_section_scale": cross_section_scale,
                     "inputs": asdict(inputs)})

    # Use the source's own neutral-D outputs at each of these pressures; no
    # interpolated or extrapolated neutral-D law is silently introduced.
    for pressure, d_o, d_o2 in ((1, 8.8, 6.3), (10, 1.2, .84)):
        for power in (100, 500, 1500):
            for mode in ("explicit_h", "gudmundsson_2000"):
                add(f"paired-{pressure}mtorr-{power}-{mode}", "paired",
                    replace(baseline, pressure_pa=pressure*.13332236842105263,
                            absorbed_power_w=power, diffusion_o_m2_s=d_o,
                            diffusion_o2_m2_s=d_o2, transport_mode=mode,
                            axial_edge_factor=.2 if mode == "explicit_h" else None,
                            radial_edge_factor=.2 if mode == "explicit_h" else None))
    # Test whether the new closure remains numerically consistent across the
    # earlier surface/diffusion hypotheses, without changing source rates.
    for scale in (.5, 1, 2):
        for gamma_o in (.15, .17, .19):
            for gamma_meta in (.001, .007, .03):
                add(f"cross-D{scale:g}-gO{gamma_o:g}-gM{gamma_meta:g}", "cross",
                    replace(baseline, diffusion_o_m2_s=1.2*scale,
                            diffusion_o2_m2_s=.84*scale,
                            gamma_o=gamma_o, gamma_meta=gamma_meta), scale)
    for temperature in (300, 1200):
        add(f"ion-temperature-{temperature}", "ion_temperature",
            replace(baseline, ion_temperature_k=temperature))
    for scale in (.5, 2):
        add(f"cross-section-{scale:g}", "cross_section",
            replace(baseline, ion_momentum_cross_sections_m2={
                "O": 7.5e-19*scale, "O2": 7.5e-19*scale}),
            cross_section_scale=scale)
    for nu in (0, 1e8):
        add(f"electron-nu-{nu:g}", "electron_nu",
            replace(baseline, electron_momentum_nu_s={"O": nu, "O2": nu}))
    return rows


def source_hashes() -> dict:
    return {path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in (
        "backend/app/oxygen_study.py", "backend/app/oxygen_transport.py",
        "backend/app/oxygen.py", "backend/app/data/oxygen-gudmundsson-2001.json",
        "backend/app/plasma_models.py", "scripts/oxygen_transport_study.py",
        "scripts/oxygen_parameter_study.py", "scripts/check_oxygen_study_results.py")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"reports/oxygen-transport-study")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-nfev", type=int, default=2500)
    parser.add_argument("--multistarts", type=int, default=3)
    parser.add_argument("--max-cases", type=int)
    args = parser.parse_args()
    if min(args.workers, args.max_nfev, args.multistarts) < 1:
        parser.error("workers, max-nfev and multistarts must be positive")
    cases = planned_cases()
    if args.max_cases is not None:
        if args.max_cases < 1:
            parser.error("max-cases must be positive")
        cases = cases[:args.max_cases]
    args.output.mkdir(parents=True, exist_ok=True)
    source = load_oxygen_data()
    hashes = source_hashes()
    # Preserve the sources actually run so subsequent model improvements do
    # not make this export's recorded checksums unverifiable.
    snapshot = args.output/"source-snapshot"
    for relative in hashes:
        saved = snapshot/relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/relative, saved)
    import numpy
    import scipy
    unique = {input_key(case["inputs"]): case["inputs"] for case in cases}
    metadata = {
        "model_status": "exploratory_published_2000_transport_not_validated",
        "case_rows": len(cases), "unique_inputs": len(unique),
        "source_sha256": hashes,
        "source_snapshot_root": str(snapshot.resolve().relative_to(ROOT)),
        "runtime": {"python": sys.version, "numpy": numpy.__version__, "scipy": scipy.__version__},
        "dataset_sha256": hashes["backend/app/data/oxygen-gudmundsson-2001.json"],
        "solver_sha256": hashes["backend/app/oxygen_study.py"],
        "study_sha256": hashes["scripts/oxygen_transport_study.py"],
        "study_script": "scripts/oxygen_transport_study.py",
        "gas": "pure O2", "chemistry_source": source["reference"],
        "transport_reference": {"doi": "10.1088/0022-3727/33/11/311",
            "url": "https://doi.org/10.1088/0022-3727/33/11/311",
            "location": "p1329 equations17-19"},
        "power_definition": "Total prescribed absorbed plasma power; not generator power or a 40MHz voltage-derived input",
        "energy_scope": "2001Eq3 reduced ground-neutral collisional closure; not complete reaction energies",
        "alpha_convention": "Sum of three volume-averaged negative-ion densities / ne; extension of the 2000 O- approximation",
        "ion_temperature": "Assume Ti=Tg=600K unless explicitly varied; not a measured ion or surface temperature",
        "cross_sections": "Atomic O7.5e-19m2 from source; equal O2 cross section adopted as an assumption; .5/2 scales are hypotheses",
        "neutral_diffusion": "Use each source pressure's reference D output, fixed across power unless scaled explicitly",
        "comparison": "Matched pairs change ion-wall transport only; all48 particle reaction coefficients remain fixed",
        "transport_domain": "Export separate necessary ordering checks; numerical success is not a transport-regime or physical validation pass",
        "not_implemented": "2001Eq8-16 core/edge width and density-normalization closure; no arbitrary core thickness was selected",
        "surface": "Source SUS/Fe probabilities used for comparison; not Si measurements; Tg is not Ts",
        "references": source["comparison_points"],
        "reference_role": "Source model predictions, not experimental measurements; signed difference is descriptive, not a fitted acceptance criterion",
        "execution": {"workers": args.workers, "max_nfev": args.max_nfev, "multistarts": args.multistarts},
    }
    (args.output/"study-plan.json").write_text(json.dumps(
        {"metadata": metadata, "cases": cases}, indent=2, ensure_ascii=False))
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    results = {}
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers,
            mp_context=multiprocessing.get_context("spawn")) as pool:
        pending = {pool.submit(evaluate, inputs, args.max_nfev, args.multistarts): key
                   for key, inputs in unique.items()}
        for future in as_completed(pending):
            results[pending[future]] = future.result()
            print(f"Completed {len(results)}/{len(unique)}; numerically converged="
                  f"{sum(bool(r.get('success')) for r in results.values())}", flush=True)
            (args.output/"checkpoint.json").write_text(json.dumps(results,
                indent=2, ensure_ascii=False, allow_nan=False))
    records = [{**case, "input_sha256": input_key(case["inputs"]),
                "result": results[input_key(case["inputs"])]} for case in cases]
    rows = []
    for case in records:
        r = case["result"]
        row = compact_row(case, r, source["comparison_points"])
        transport = r.get("transport", {})
        row.update({"transport_mode": case["inputs"]["transport_mode"],
            "transport_domain_valid": transport.get("domain_valid"),
            "lambda_i_m": transport.get("ion_mean_free_path_m"),
            "ion_temperature_k": transport.get("ion_temperature_k"),
            "cross_section_scale": case["cross_section_scale"],
            "transport_warnings": ";".join(transport.get("domain_warnings", []))})
        row["hL"] = transport.get("axial_edge_factor", row["hL"])
        row["hR"] = transport.get("radial_edge_factor", row["hR"])
        rows.append(row)
    metadata.update({"elapsed_seconds": time.monotonic()-started,
        "unique_converged": sum(bool(r.get("success")) for r in results.values()),
        "row_converged": sum(row["converged"] for row in rows)})
    (args.output/"results.json").write_text(json.dumps({"metadata": metadata,
        "cases": records}, indent=2, ensure_ascii=False, allow_nan=False))
    with (args.output/"cases.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"output": str(args.output), "unique": len(unique),
        "converged": metadata["unique_converged"], "seconds": metadata["elapsed_seconds"]}))


if __name__ == "__main__":
    main()

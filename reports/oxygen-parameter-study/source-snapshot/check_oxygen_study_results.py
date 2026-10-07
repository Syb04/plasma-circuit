#!/usr/bin/env python3
"""Independently recalculate exported O2 budgets and check source hashes."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.oxygen import load_oxygen_data, oxygen_excitation_losses, oxygen_rates
from app.plasma_models import ATOMIC_MASS, BOLTZMANN, ELEMENTARY_CHARGE, ELECTRON_MASS


def relative_error(a: float, b: float) -> float:
    return abs(a-b)/max(abs(a), abs(b), 1e-100)


def verify(path: Path) -> dict:
    export = json.loads(path.read_text())
    metadata = export["metadata"]
    for key, file in {
        "dataset_sha256": ROOT / "backend/app/data/oxygen-gudmundsson-2001.json",
        "solver_sha256": ROOT / "backend/app/oxygen_study.py",
        "study_sha256": ROOT / "scripts/oxygen_parameter_study.py",
    }.items():
        assert hashlib.sha256(file.read_bytes()).hexdigest() == metadata[key], key
    source = load_oxygen_data()
    species = {s["name"]: s for s in source["species"]}
    unique = {case["input_sha256"]: case for case in export["cases"]}
    checked, failures, max_event_error, max_budget_error = 0, [], 0.0, 0.0
    for case in unique.values():
        result = case["result"]
        if not result.get("success"):
            failures.append({"case_id": case["case_id"],
                             "reasons": result.get("failure_reasons", [result.get("error")])})
            continue
        p, n, te = result["inputs"], result["densities_m3"], result["temperature_ev"]
        assert 1 < te < 4.5 and result["source_domain_valid"]
        assert all(value > 0 and math.isfinite(value) for value in n.values())
        assert all(result["residuals"]["checks"].values())
        charge_terms = [species[name]["charge"]*value for name, value in n.items()]
        assert abs(math.fsum(charge_terms))/max(math.fsum(map(abs, charge_terms)), 1) < 1e-10
        coefficients = oxygen_rates(te, p["gas_temperature_k"])
        production, loss = dict.fromkeys(n, 0.0), dict.fromkeys(n, 0.0)
        for reaction in source["reactions"]:
            if not reaction["implemented"]:
                continue
            event = coefficients[reaction["id"]]
            for name, count in reaction["reactants"].items():
                event *= n[name]**count
            discrepancy = relative_error(event, result["reaction_event_rates_m3_s"][reaction["id"]])
            assert discrepancy < 1e-11, (case["case_id"], reaction["id"], discrepancy)
            max_event_error = max(max_event_error, discrepancy)
            for name in n:
                delta = reaction["products"].get(name, 0)-reaction["reactants"].get(name, 0)
                production[name] += max(delta, 0)*event
                loss[name] += max(-delta, 0)*event
        for wall in source["wall_reactions"]:
            name = next(iter(wall["reactants"]))
            event = result["wall_rates_s"][wall["id"]]*n[name]
            loss[name] += event
            for product, count in wall["products"].items():
                production[product] += event*count
        ion_wall = result["wall_rates_s"]["k50"]*n["O+"] + result["wall_rates_s"]["k51"]*n["O2+"]
        loss["e"] += ion_wall
        feed = p["flow_sccm"]*1e-6/60*101325/(BOLTZMANN*273.15)
        volume = math.pi*p["radius_m"]**2*p["length_m"]
        neutral = [name for name in n if species[name]["charge"] == 0]
        production["O2"] += feed/volume
        for name in neutral:
            loss[name] += result["pumping_rate_s"]*n[name]
        for name in n:
            assert relative_error(production[name], loss[name]) < 1e-6, (case["case_id"], name)
        atom_feed = 2*feed/volume
        atom_out = result["pumping_rate_s"]*sum(species[name]["elements"].get("O", 0)*n[name] for name in neutral)
        assert relative_error(atom_feed, atom_out) < 1e-6
        neutral_target = p["pressure_pa"]/(BOLTZMANN*p["gas_temperature_k"])
        assert relative_error(sum(n[name] for name in neutral), neutral_target) < 1e-6
        pump0 = feed/volume/neutral_target
        assert 2*pump0/3*(1-1e-6) <= result["pumping_rate_s"] <= 2*pump0*(1+1e-6)
        excitation = oxygen_excitation_losses(te)
        e, ne = ELEMENTARY_CHARGE, n["e"]
        energies = source["supplementary_ionization_energies_ev"]
        expected = {
            "ionization": e*volume*ne*(n["O2"]*coefficients["k1"]*energies["O2"]["value"] + n["O"]*coefficients["k4"]*energies["O"]["value"]),
            "excitation": e*volume*ne*sum(n[name]*excitation[name] for name in ("O", "O2")),
            "elastic": e*volume*ne*te*sum(3*ELECTRON_MASS/(species[name]["mass_amu"]*ATOMIC_MASS)*nu for name, nu in p["electron_momentum_nu_s"].items()),
            "electron_wall": e*volume*2*te*ion_wall,
            "ion_wall": e*volume*(result["plasma_potential_v"]+.5*te)*ion_wall,
        }
        for key, value in expected.items():
            discrepancy = relative_error(value, result["loss_w"][key])
            assert discrepancy < 1e-10, (case["case_id"], key, discrepancy)
            max_budget_error = max(max_budget_error, discrepancy)
        assert relative_error(sum(expected.values()), p["absorbed_power_w"]) < 1e-6
        area = 2*math.pi*p["radius_m"]*(p["radius_m"]+p["length_m"])
        electron_flux = ne*math.sqrt(e*te/(2*math.pi*ELECTRON_MASS))*math.exp(-result["plasma_potential_v"]/te)*area/volume
        assert relative_error(electron_flux, ion_wall) < 1e-10
        checked += 1
    return {"unique_inputs": len(unique), "independently_verified_successes": checked,
            "failed_inputs_retained": failures, "maximum_event_recalculation_relative_error": max_event_error,
            "maximum_budget_recalculation_relative_error": max_budget_error,
            "interpretation": "Independent numerical accounting under the declared reduced closure; not physical validation"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "reports/oxygen-parameter-study/results.json")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/oxygen-parameter-study/verification.json")
    args = parser.parse_args()
    summary = verify(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

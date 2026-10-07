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


def verify_source_hashes(metadata: dict) -> dict:
    """Check the recorded source set, including preserved pre-change sources."""
    if "source_sha256" in metadata:
        matched = {}
        for relative, expected in metadata["source_sha256"].items():
            path = (ROOT/relative).resolve()
            assert path.is_relative_to(ROOT), relative
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                path = (ROOT/metadata["source_snapshot_root"]/relative).resolve()
                assert path.is_relative_to(ROOT), relative
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative
            matched[relative] = str(path.relative_to(ROOT))
        return {"mode": "recorded_sources", "files": matched}
    matched = {}
    for key, path in {
        "dataset_sha256": ROOT / "backend/app/data/oxygen-gudmundsson-2001.json",
        "solver_sha256": ROOT / "backend/app/oxygen_study.py",
        "study_sha256": ROOT / metadata.get("study_script", "scripts/oxygen_parameter_study.py"),
    }.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata[key]:
            path = ROOT/"reports/oxygen-parameter-study/source-snapshot"/path.name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata[key], key
        matched[key] = str(path.relative_to(ROOT))
    return {"mode": "recorded_legacy_sources", "files": matched}


def expected_wall_rates(p: dict, n: dict, te: float) -> tuple[dict, dict]:
    """Reconstruct transport without calling either solver or closure module."""
    r, length, tg = p["radius_m"], p["length_m"], p["gas_temperature_k"]
    volume = math.pi*r*r*length
    area = 2*math.pi*r*(r+length)
    mode = p.get("transport_mode", "explicit_h")
    transport = {"mode": mode}
    if mode == "explicit_h":
        h_l, h_r = p["axial_edge_factor"], p["radial_edge_factor"]
    else:
        assert mode == "gudmundsson_2000", mode
        cross_sections = p["ion_momentum_cross_sections_m2"]
        mean_free_path = 1/sum(n[name]*cross_sections[name] for name in ("O", "O2"))
        ti = p["ion_temperature_k"] if p["ion_temperature_k"] is not None else tg
        gamma = ELEMENTARY_CHARGE*te/(BOLTZMANN*ti)
        alpha = sum(n[name] for name in ("O-", "O2-", "O3-"))/n["e"]
        correction = (1+3*alpha/gamma)/(1+alpha)
        h_l = .86*correction/math.sqrt(3+length/(2*mean_free_path))
        h_r = .8*correction/math.sqrt(4+r/mean_free_path)
        transport.update({"axial_edge_factor": h_l, "radial_edge_factor": h_r,
            "ion_mean_free_path_m": mean_free_path, "ion_temperature_k": ti,
            "domain_valid": mean_free_path <= min(r, length)
                and mean_free_path >= max(r, length)/gamma and h_l <= 1 and h_r <= 1})
    geometry = 2*(r*r*h_l+r*length*h_r)/(r*r*length)
    rates = {key: geometry*math.sqrt(ELEMENTARY_CHARGE*te/(mass*ATOMIC_MASS))
             for key, mass in (("k50", 15.999), ("k51", 31.998))}
    diffusion_length_sq = 1/((math.pi/length)**2+(2.405/r)**2)
    for key, mass, diffusion, probability in (
        ("k52", 15.999, p["diffusion_o_m2_s"], p["gamma_o"]),
        ("k53", 15.999, p["diffusion_o_m2_s"], p["gamma_o"]),
        ("k54", 31.998, p["diffusion_o2_m2_s"], p["gamma_meta"])):
        speed = math.sqrt(8*BOLTZMANN*tg/(math.pi*mass*ATOMIC_MASS))
        rates[key] = (1/(diffusion_length_sq/diffusion
            +2*volume*(2-probability)/(area*speed*probability))) if probability > 0 else 0
    return rates, transport


def verify(path: Path) -> dict:
    export = json.loads(path.read_text())
    metadata = export["metadata"]
    hashes = verify_source_hashes(metadata)
    source = load_oxygen_data()
    species = {s["name"]: s for s in source["species"]}
    unique = {case["input_sha256"]: case for case in export["cases"]}
    checked, failures, max_event_error, max_budget_error, max_wall_error = 0, [], 0.0, 0.0, 0.0
    outside_transport_ordering = []
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
        wall_rates, transport = expected_wall_rates(p, n, te)
        for key, value in wall_rates.items():
            discrepancy = relative_error(value, result["wall_rates_s"][key])
            assert discrepancy < 1e-10, (case["case_id"], key, discrepancy)
            max_wall_error = max(max_wall_error, discrepancy)
        if transport["mode"] != "explicit_h":
            reported = result["transport"]
            for key in ("axial_edge_factor", "radial_edge_factor", "ion_mean_free_path_m", "ion_temperature_k"):
                assert relative_error(transport[key], reported[key]) < 1e-10, (case["case_id"], key)
            assert transport["domain_valid"] == reported["domain_valid"]
            assert transport["domain_valid"] == result["transport_domain_valid"]
            if not transport["domain_valid"]:
                outside_transport_ordering.append(case["case_id"])
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
            "maximum_wall_rate_recalculation_relative_error": max_wall_error,
            "numerical_successes_outside_transport_ordering": outside_transport_ordering,
            "source_hash_verification": hashes,
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

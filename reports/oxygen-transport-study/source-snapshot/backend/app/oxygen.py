"""Documented oxygen chemistry from Gudmundsson et al. (2001).

The source tables contain typographical inconsistencies.  The original text,
the conservative normalizations, and the unimplemented reaction are retained in
the JSON data.  This module does not evaluate strings supplied by a caller.
The dataset is a literature-reproduction candidate for stainless steel, and is
not a validated silicon-electrode model.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from .plasma_models import ATOMIC_MASS, BOLTZMANN, ELEMENTARY_CHARGE

OXYGEN_MODEL_VERSION = "o2-gudmundsson-2001-0.1"
OXYGEN_DATA_PATH = Path(__file__).with_name("data") / "oxygen-gudmundsson-2001.json"


def load_oxygen_data() -> dict[str, Any]:
    """Return a fresh copy of the machine-readable source transcription."""
    return json.loads(OXYGEN_DATA_PATH.read_text(encoding="utf-8"))


def _temperature(value: float, name: str) -> float:
    t = float(value)
    if not math.isfinite(t) or t <= 0:
        raise ValueError(f"{name} must be a positive finite temperature")
    return t


def _published_fit(spec: dict[str, Any], te: float, tg: float) -> float:
    """Explicit algebra for the two published fit families, never eval."""
    kind = spec["kind"]
    if kind == "te_arrhenius":
        return float(spec["prefactor"]) * te**float(spec.get("te_power", 0)) * math.exp(
            -float(spec.get("activation_ev", 0)) / te)
    if kind == "tg_power":
        return float(spec["prefactor"]) * (300.0 / tg)**float(spec.get("tg_power", 0))
    if kind == "constant":
        return float(spec["coefficient"])
    raise ValueError(f"Unsupported published fit family: {kind}")


def oxygen_rates(temperature_ev: float, gas_temperature_k: float = 600.0) -> dict[str, float]:
    """k1..k49, normalized to m³/s or m⁶/s as specified in the JSON.

    All electron-impact fits are restricted to their published Maxwellian
    range, 1--7 eV. k49 is returned for documentary purposes; its printed
    reaction does not conserve charge and is not in the runnable network.
    """
    te = _temperature(temperature_ev, "Te")
    tg = _temperature(gas_temperature_k, "Tg")
    if not 1.0 <= te <= 7.0:
        raise ValueError("Gudmundsson (2001) Maxwellian fits require 1–7 eV")
    return {row["id"]: _published_fit(row["rate_formula"], te, tg)
            for row in load_oxygen_data()["reactions"]}


def oxygen_network_data(gas_temperature_k: float = 600.0) -> dict[str, Any]:
    """Build the conservative particle network; energy closure is separate.

    Zero energy losses in this adapter mean particle-only evaluation, not a
    complete electron-energy model. Call ``oxygen_excitation_losses`` for
    the independent collisional energy terms. The separate literature solver
    must supply wall, flow, elastic, and charged-particle energy closures.
    """
    tg = _temperature(gas_temperature_k, "Tg")
    data = load_oxygen_data()
    ts = [1.0 + i / 100.0 for i in range(601)]
    rows = []
    for row in data["reactions"]:
        if not row["implemented"]:
            continue
        formula = row["rate_formula"]
        rate = {"type": "constant", "coefficient": _published_fit(formula, 3, tg),
                "units": row["rate_units"]}
        if formula["kind"] == "te_arrhenius":
            rate = {"temperature_ev": ts,
                    "coefficients": [_published_fit(formula, t, tg) for t in ts],
                    "units": row["rate_units"]}
        rows.append({"name": row["id"], "reactants": copy.deepcopy(row["reactants"]),
                     "products": copy.deepcopy(row["products"]), "rate": rate,
                     "electron_energy_loss_ev": 0.0, "source": row["source"]})
    return {"name": data["name"], "version": OXYGEN_MODEL_VERSION, "gas": "O2",
            "source": data["reference"]["url"], "temperature_range_ev": [1.0, 7.0],
            "species": [{"name": s["name"], "charge": s["charge"],
                         "mass_amu": s["mass_amu"], "elements": s["elements"],
                         "reservoir": s["name"] == "O2", "wall_loss_model": "constant"}
                        for s in data["species"] if s["name"] != "e"],
            "reactions": rows, "status": data["status"],
            "energy_mode": "particle_only_separate_literature_energy_closure_required",
            "omitted_reactions": data["omitted_reactions"],
            "source_corrections": data["source_corrections"]}


def neutral_wall_loss_rate(diffusion_m2_s: float, mass_amu: float,
                           gas_temperature_k: float, radius_m: float,
                           length_m: float, sticking_probability: float) -> float:
    """Source eqs. (4),(6), with Tg converted from K using k_B.

    A measured/derived diffusion coefficient is an explicit input.  Surface
    probability is dimensionless; the returned first-order rate is s^-1.
    """
    d, mass, tg, r, length = (_temperature(v, name) for v, name in (
        (diffusion_m2_s, "D"), (mass_amu, "mass"), (gas_temperature_k, "Tg"),
        (radius_m, "R"), (length_m, "L")))
    gamma = float(sticking_probability)
    if not math.isfinite(gamma) or not 0 <= gamma <= 1:
        raise ValueError("Wall probability must be in [0,1]")
    if gamma == 0:
        return 0.0
    volume = math.pi * r*r * length
    area = 2 * math.pi * r * (r + length)
    diffusion_length_sq = 1 / ((math.pi / length)**2 + (2.405 / r)**2)
    speed = math.sqrt(8 * BOLTZMANN * tg / (math.pi * mass * ATOMIC_MASS))
    return 1 / (diffusion_length_sq / d + 2 * volume * (2 - gamma) / (area * speed * gamma))


def oxygen_wall_rates(temperature_ev: float, gas_temperature_k: float,
                      radius_m: float, length_m: float, *,
                      axial_edge_factor: float, radial_edge_factor: float | None = None,
                      diffusion_coefficients_m2_s: dict[str, float]) -> dict[str, float]:
    """Table 2 k50..54 with explicit transport/edge inputs.

    Paper assumes hR=hL for its short cylinder.  The factors are supplied so
    this helper does not silently replace eqs. (8)--(16) with a guessed value.
    O(1D) shares atomic diffusion and O2(a) shares molecular diffusion, as in
    the paper's eq. (5) species treatment; explicit state entries take priority.
    """
    te, tg = _temperature(temperature_ev, "Te"), _temperature(gas_temperature_k, "Tg")
    r, length = _temperature(radius_m, "R"), _temperature(length_m, "L")
    hl = float(axial_edge_factor)
    hr = hl if radial_edge_factor is None else float(radial_edge_factor)
    if any(not math.isfinite(h) or not 0 < h <= 1 for h in (hl, hr)):
        raise ValueError("Sheath-edge factors must be in (0,1]")
    species = {s["name"]: s for s in load_oxygen_data()["species"]}
    geometry = 2 * (r*r*hl + r*length*hr) / (r*r*length)
    rates = {"k50": geometry * math.sqrt(ELEMENTARY_CHARGE * te / (species["O+"]["mass_amu"] * ATOMIC_MASS)),
             "k51": geometry * math.sqrt(ELEMENTARY_CHARGE * te / (species["O2+"]["mass_amu"] * ATOMIC_MASS))}
    for k, name, fallback, gamma in (("k52", "O", "O", 0.17),
                                     ("k53", "O_1D", "O", 0.17),
                                     ("k54", "O2_a", "O2", 0.007)):
        d = diffusion_coefficients_m2_s.get(name, diffusion_coefficients_m2_s.get(fallback))
        if d is None:
            raise ValueError(f"An explicit diffusion coefficient is required for {name}")
        rates[k] = neutral_wall_loss_rate(d, species[name]["mass_amu"], tg, r, length, gamma)
    return rates


def oxygen_excitation_losses(temperature_ev: float) -> dict[str, float]:
    """Sum E_ex*k_ex for each neutral (eV m³/s), source-26 table 3.

    Ionization and elastic transfer are deliberately separate.  An Arrhenius
    fit's activation parameter is not its physical excitation threshold.
    """
    te = _temperature(temperature_ev, "Te")
    if not 1 <= te <= 7:
        raise ValueError("Published Maxwellian excitation fits require 1–7 eV")
    data = load_oxygen_data()
    if not data.get("electron_excitation"):
        raise ValueError("Source-26 excitation table has not been transcribed")
    return {name: sum(float(row["threshold_ev"]) * _published_fit(row["rate_formula"], te, 600)
                      for row in data["electron_excitation"] if row["target"] == name)
            for name in ("O", "O2")}

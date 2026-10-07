"""Exploratory steady oxygen global model with explicit transport inputs.

This closes a *declared reduced* energy budget, not the energies of all 48
particle reactions.  Numerical closure is not literature validation.  The
source's unresolved k49 is excluded, and no coefficient is fitted to data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import math
from typing import Any, Mapping

import numpy as np
from scipy.optimize import least_squares
from scipy.special import expit

from .oxygen import load_oxygen_data, neutral_wall_loss_rate
from .plasma_models import ATOMIC_MASS, BOLTZMANN, ELEMENTARY_CHARGE

ELECTRON_MASS_KG = 9.1093837139e-31
STANDARD_TEMPERATURE_K = 273.15
STANDARD_PRESSURE_PA = 101325.0
NEUTRAL_NAMES = ("O2", "O2_a", "O", "O_1D", "O3")
NEGATIVE_NAMES = ("O-", "O2-", "O3-")
POSITIVE_NAMES = ("O+", "O2+")
HEAVY_NAMES = NEUTRAL_NAMES + NEGATIVE_NAMES + POSITIVE_NAMES
ALL_NAMES = HEAVY_NAMES + ("e",)
MODEL_VERSION = "oxygen-explicit-input-study-0.1"


@dataclass(frozen=True)
class StudyInputs:
    """SI inputs; nu is a target-resolved collision frequency per electron.

    ``electron_momentum_nu_s`` requires O and O2 entries, in s^-1, including
    explicitly supplied zeros.  These are frequencies, not rate coefficients;
    elastic loss therefore does not multiply them by neutral density again.
    Defaults below reproduce the source geometry/flow and surface candidates,
    not a measurement or a calibrated transport closure for this case.
    """

    absorbed_power_w: float
    pressure_pa: float
    axial_edge_factor: float
    radial_edge_factor: float
    diffusion_o_m2_s: float
    diffusion_o2_m2_s: float
    electron_momentum_nu_s: Mapping[str, float]
    gas_temperature_k: float = 600.0
    radius_m: float = 0.152
    length_m: float = 0.076
    flow_sccm: float = 50.0
    gamma_o: float = 0.17
    gamma_meta: float = 0.007

    def __post_init__(self) -> None:
        for name in ("absorbed_power_w", "pressure_pa", "gas_temperature_k",
                     "radius_m", "length_m", "flow_sccm", "diffusion_o_m2_s",
                     "diffusion_o2_m2_s"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
            object.__setattr__(self, name, value)
        for name in ("axial_edge_factor", "radial_edge_factor"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0,1]")
            object.__setattr__(self, name, value)
        for name in ("gamma_o", "gamma_meta"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0,1]")
            object.__setattr__(self, name, value)
        if set(self.electron_momentum_nu_s) != {"O", "O2"}:
            raise ValueError("electron_momentum_nu_s requires exactly O and O2")
        frequencies = {name: float(value) for name, value in self.electron_momentum_nu_s.items()}
        if any(not math.isfinite(value) or value < 0 for value in frequencies.values()):
            raise ValueError("electron momentum frequencies must be nonnegative and finite")
        object.__setattr__(self, "electron_momentum_nu_s", frequencies)


def sccm_to_particles_s(flow_sccm: float) -> float:
    """Molecules per second at explicitly defined 273.15 K, 101325 Pa STP."""
    flow = float(flow_sccm)
    if not math.isfinite(flow) or flow <= 0:
        raise ValueError("flow_sccm must be positive and finite")
    return flow * 1e-6 / 60.0 * STANDARD_PRESSURE_PA / (BOLTZMANN * STANDARD_TEMPERATURE_K)


@lru_cache(maxsize=1)
def _chemistry() -> dict[str, Any]:
    data = load_oxygen_data()
    index = {name: i for i, name in enumerate(ALL_NAMES)}
    rows = [row for row in data["reactions"] if row["implemented"]]
    powers = np.zeros((len(rows), len(ALL_NAMES)))
    net = np.zeros((len(ALL_NAMES), len(rows)))
    prefactors, te_powers, activations = [], [], []
    for j, row in enumerate(rows):
        for name, count in row["reactants"].items():
            powers[j, index[name]] = count
            net[index[name], j] -= count
        for name, count in row["products"].items():
            net[index[name], j] += count
        formula = row["rate_formula"]
        prefactors.append(float(formula.get("prefactor", formula.get("coefficient", 0))))
        te_powers.append(float(formula.get("te_power", 0)))
        activations.append(float(formula.get("activation_ev", 0)))
    species = {s["name"]: s for s in data["species"]}
    return {"data": data, "rows": rows, "powers": powers, "net": net,
            "produce": np.maximum(net, 0), "consume": np.maximum(-net, 0),
            "prefactors": np.asarray(prefactors), "te_powers": np.asarray(te_powers),
            "activations": np.asarray(activations), "species": species,
            "atoms": np.asarray([species[n]["elements"].get("O", 0) for n in ALL_NAMES]),
            "charges": np.asarray([species[n]["charge"] for n in ALL_NAMES])}


class _CaseModel:
    def __init__(self, inputs: StudyInputs):
        self.inputs = inputs
        self.chemistry = _chemistry()
        self.volume = math.pi * inputs.radius_m**2 * inputs.length_m
        self.area = 2 * math.pi * inputs.radius_m * (inputs.radius_m + inputs.length_m)
        self.neutral_reference = inputs.pressure_pa / (BOLTZMANN * inputs.gas_temperature_k)
        self.feed_s = sccm_to_particles_s(inputs.flow_sccm)
        self.feed_m3_s = self.feed_s / self.volume
        self.pump_reference = self.feed_m3_s / self.neutral_reference
        self.rate_prefactors = self.chemistry["prefactors"].copy()
        for j, row in enumerate(self.chemistry["rows"]):
            formula = row["rate_formula"]
            if formula["kind"] == "tg_power":
                self.rate_prefactors[j] *= (300 / inputs.gas_temperature_k)**float(formula.get("tg_power", 0))
        # Eqs.(4),(6); explicit gammas replace, rather than supplement, wall k52..54.
        self.neutral_wall = np.asarray([
            neutral_wall_loss_rate(inputs.diffusion_o_m2_s, 15.999,
                                   inputs.gas_temperature_k, inputs.radius_m,
                                   inputs.length_m, inputs.gamma_o),
            neutral_wall_loss_rate(inputs.diffusion_o_m2_s, 15.999,
                                   inputs.gas_temperature_k, inputs.radius_m,
                                   inputs.length_m, inputs.gamma_o),
            neutral_wall_loss_rate(inputs.diffusion_o2_m2_s, 31.998,
                                   inputs.gas_temperature_k, inputs.radius_m,
                                   inputs.length_m, inputs.gamma_meta)])
        self.ion_geometry = 2 * (inputs.radius_m**2 * inputs.axial_edge_factor
                                  + inputs.radius_m * inputs.length_m * inputs.radial_edge_factor) / (
                                      inputs.radius_m**2 * inputs.length_m)
        self.lower = np.asarray([-70.0]*9 + [-30.0, 0.0, math.log(.001)])
        # Table fits generally span 1--7 eV, but source section 3.4 explicitly
        # restricts attachment k20 to 1<Te<4.5 eV. Every run includes k20.
        self.upper = np.asarray([math.log(10.0)]*9 + [30.0, math.log(4.5), math.log(1000.0)])

    def unpack(self, x: np.ndarray) -> tuple[np.ndarray, float, float]:
        densities = np.zeros(len(ALL_NAMES))
        densities[:8] = self.neutral_reference * np.exp(x[:8])
        ne = self.neutral_reference * math.exp(x[8])
        positive_total = ne + float(np.sum(densities[5:8]))
        fraction = expit(x[9])
        densities[8:10] = positive_total * np.asarray([fraction, 1 - fraction])
        densities[10] = ne
        return densities, math.exp(x[10]), self.pump_reference * math.exp(x[11])

    def evaluate(self, x: np.ndarray) -> dict[str, Any]:
        densities, te, pump = self.unpack(x)
        chemistry = self.chemistry
        rate_constants = self.rate_prefactors * te**chemistry["te_powers"] * np.exp(-chemistry["activations"] / te)
        events = rate_constants * np.exp(chemistry["powers"] @ np.log(densities))
        production = chemistry["produce"] @ events
        loss = chemistry["consume"] @ events
        ion_wall = self.ion_geometry * np.sqrt(ELEMENTARY_CHARGE * te / (np.asarray([15.999, 31.998]) * ATOMIC_MASS))
        wall_rates = np.concatenate((ion_wall, self.neutral_wall))
        wall_names = (8, 9, 2, 3, 1)
        wall_events = wall_rates * densities[list(wall_names)]
        # k50/51: neutral return; k52/53: two atoms form one O2; k54: quench.
        for event, source, target, coefficient in zip(wall_events, wall_names, (2, 0, 0, 0, 0), (1, 1, .5, .5, 1)):
            loss[source] += event
            production[target] += coefficient * event
        electron_wall_m3_s = float(np.sum(wall_events[:2]))
        loss[10] += electron_wall_m3_s
        loss[:5] += pump * densities[:5]
        production[0] += self.feed_m3_s
        ve = math.sqrt(ELEMENTARY_CHARGE * te / (2 * math.pi * ELECTRON_MASS_KG))
        phi = te * math.log(densities[10] * ve * self.area / (self.volume * electron_wall_m3_s))
        # Use the source eq.(3) reduced collisional closure. Additional particle
        # reaction energies have not been published/transcribed as a full table.
        thresholds = chemistry["data"]["supplementary_ionization_energies_ev"]
        ionization_ev_m3_s = events[0] * thresholds["O2"]["value"] + events[3] * thresholds["O"]["value"]
        excitation_by_target = {"O": 0.0, "O2": 0.0}
        for row in chemistry["data"]["electron_excitation"]:
            f = row["rate_formula"]
            k = float(f.get("prefactor", f.get("coefficient", 0))) * te**float(f.get("te_power", 0)) * math.exp(-float(f.get("activation_ev", 0)) / te)
            excitation_by_target[row["target"]] += row["threshold_ev"] * k
        excitation_ev_m3_s = densities[10] * (
            densities[2] * excitation_by_target["O"] + densities[0] * excitation_by_target["O2"])
        elastic_ev_m3_s = densities[10] * te * sum(
            3 * ELECTRON_MASS_KG / (chemistry["species"][name]["mass_amu"] * ATOMIC_MASS) * nu
            for name, nu in self.inputs.electron_momentum_nu_s.items())
        ev_to_w = ELEMENTARY_CHARGE * self.volume
        loss_w = {"ionization": ionization_ev_m3_s * ev_to_w,
                  "excitation": excitation_ev_m3_s * ev_to_w,
                  "elastic": elastic_ev_m3_s * ev_to_w,
                  "electron_wall": 2 * te * electron_wall_m3_s * ev_to_w,
                  "ion_wall": (phi + te / 2) * electron_wall_m3_s * ev_to_w}
        loss_w["total"] = math.fsum(loss_w.values())
        return {"densities": densities, "te": te, "pump": pump, "production": production,
                "loss": loss, "events": events, "wall_rates": wall_rates,
                "electron_wall_m3_s": electron_wall_m3_s, "phi": phi, "loss_w": loss_w}

    def residual(self, x: np.ndarray) -> np.ndarray:
        state = self.evaluate(x)
        # Log production/loss ratios retain a gradient at very small densities.
        # The post-solve acceptance check instead uses un-floored P/L scales.
        floor = self.feed_m3_s * 1e-40
        species = np.log((state["production"][:10] + floor) / (state["loss"][:10] + floor))
        pressure = math.log(float(np.sum(state["densities"][:5])) / self.neutral_reference)
        power = max(state["loss_w"]["total"], self.inputs.absorbed_power_w * 1e-100)
        energy = math.log(power / self.inputs.absorbed_power_w)
        sheath = min(state["phi"] / state["te"], 0.0)
        return np.concatenate((species, [pressure, energy, sheath]))

    def seed(self, number: int) -> np.ndarray:
        # Physically populated molecular / mixed / dissociated states. Minor
        # negative species are seeded nonzero; no extinct plasma seed is used.
        fractions = ((.65, .025, .31, .014, .001),
                     (.25, .015, .70, .034, .001),
                     (.90, .030, .060, .009, .001),
                     (.08, .008, .86, .051, .001))
        neutral = np.asarray(fractions[number % len(fractions)])
        te = (3.0, 2.2, 4.2, 1.6)[number % 4]
        mean_energy = 60.0
        ne = min(.08 * self.neutral_reference, max(1e12,
                 self.inputs.absorbed_power_w / (self.volume * ELEMENTARY_CHARGE * mean_energy * 2e4)))
        ne *= (1.0, .4, 2.0, 1.0)[number % 4]
        ne = min(ne, .5 * self.neutral_reference)
        negative = ne * np.asarray([.3, .003, .00001])
        neutral_atoms = float(np.dot(neutral, self.chemistry["atoms"][:5]))
        pump = 2 * self.pump_reference / neutral_atoms
        positive_fraction = (.6, .8, .2, .9)[number % 4]
        x = np.concatenate((np.log(neutral), np.log(negative / self.neutral_reference),
                            [math.log(ne / self.neutral_reference),
                             math.log(positive_fraction / (1 - positive_fraction)),
                             math.log(te), math.log(pump / self.pump_reference)]))
        return np.clip(x, self.lower + 1e-7, self.upper - 1e-7)

    def diagnostics(self, state: dict[str, Any], x: np.ndarray, tolerance: float) -> dict[str, Any]:
        density, production, loss = state["densities"], state["production"], state["loss"]
        # No ne/power common scale: a minor species must balance its own sources.
        scales = np.maximum(production, loss)
        relative = np.divide(production - loss, scales, out=np.zeros_like(scales), where=scales > 0)
        neutral_relative = (float(np.sum(density[:5])) - self.neutral_reference) / self.neutral_reference
        atom_in = 2 * self.feed_m3_s
        atom_out = state["pump"] * float(np.dot(self.chemistry["atoms"][:5], density[:5]))
        atom_relative = (atom_in - atom_out) / max(atom_in, atom_out)
        charge_terms = self.chemistry["charges"] * density
        charge_relative = float(math.fsum(charge_terms)) / max(float(np.sum(np.abs(charge_terms))), 1)
        energy_relative = (state["loss_w"]["total"] - self.inputs.absorbed_power_w) / self.inputs.absorbed_power_w
        # Reject a numerically flat density/pump/logit boundary and the strict
        # k20 temperature endpoints; hitting a solver bound is not a solution.
        bounded_indices = list(range(10)) + [11]
        boundary = any(x[i] - self.lower[i] <= 1e-6 or self.upper[i] - x[i] <= 1e-6 for i in bounded_indices)
        temperature_boundary = x[10] - self.lower[10] <= 1e-7 or self.upper[10] - x[10] <= 1e-7
        maximum = max(float(np.max(np.abs(relative))), abs(neutral_relative), abs(atom_relative),
                      abs(charge_relative), abs(energy_relative))
        finite = bool(np.all(np.isfinite(density)) and all(math.isfinite(v) for v in state["loss_w"].values()))
        checks = {"species": bool(np.max(np.abs(relative[:10])) <= tolerance),
                  "electron": bool(abs(relative[10]) <= tolerance),
                  "energy": bool(abs(energy_relative) <= tolerance),
                  "neutral_pressure": bool(abs(neutral_relative) <= tolerance),
                  "oxygen_atoms": bool(abs(atom_relative) <= tolerance),
                  "charge": bool(abs(charge_relative) <= tolerance),
                  "positive_densities": bool(np.all(density > 0)),
                  "temperature_range": bool(1 - 1e-12 <= state["te"] <= 7 + 1e-12),
                  "source_reaction_domain": bool(1 < state["te"] < 4.5 and not temperature_boundary),
                  "positive_pumping": bool(state["pump"] > 0),
                  "nonnegative_sheath": bool(state["phi"] >= 0),
                  "no_density_bound": not boundary, "no_temperature_bound": not temperature_boundary,
                  "finite": finite}
        return {"checks": checks, "species_relative": dict(zip(ALL_NAMES, map(float, relative))),
                "energy_relative": float(energy_relative), "neutral_pressure_relative": float(neutral_relative),
                "oxygen_atoms_relative": float(atom_relative), "charge_relative": float(charge_relative),
                "max_relative": float(maximum), "tolerance": tolerance,
                "oxygen_atom_in_m3_s": atom_in, "oxygen_atom_out_m3_s": atom_out}


def study_metadata(inputs: StudyInputs) -> dict[str, Any]:
    """Keep source values, supplementary data, and assumed case inputs distinct."""
    data = _chemistry()["data"]
    return {"model_version": MODEL_VERSION, "validation_status": "exploratory_not_literature_validated",
            "success_definition": "numerical particle, reduced energy, pressure, atom and charge closure only",
            "source_values": {"chemistry_reference": data["reference"]["url"],
                              "excitation_reference": data["energy_source"]["url"],
                              "implemented_reaction_count": 48, "excitation_term_count": 16,
                              "source_corrections": data["source_corrections"],
                              "omitted_reactions": data["omitted_reactions"],
                              "wall_return_reactions": data["wall_reactions"],
                              "published_conditions": data["published_conditions"]},
            "supplementary_values": {"ionization_energies_ev": data["supplementary_ionization_energies_ev"],
                                     "flow_standard_temperature_k": STANDARD_TEMPERATURE_K,
                                     "flow_standard_pressure_pa": STANDARD_PRESSURE_PA},
            "case_inputs_and_assumptions": asdict(inputs),
            "closures": {"absorbed_power": "total plasma absorbed power: collisional + electron-wall + ion-wall",
                         "energy": "source eq.(3) reduced ground-target collisional budget; not all 48 reaction energies",
                         "ionization": "ground O2 k1 and ground O k4 with supplementary NIST thresholds",
                         "excitation": "all 16 source-26 terms once; no additional per-particle excitation charge",
                         "elastic": "3*me/mi*Te*ne*nu_i; supplied nu_i are per-electron s^-1 frequencies, not m^3/s",
                         "wall_energy": "electron: 2Te; ion: phi+Te/2; each applied once to ion-balanced wall flux",
                         "sheath": "phi=Te*log(ne*sqrt(eTe/(2*pi*me))*A/(V*sum_i(kwi*ni)))",
                         "pressure": "sum of five neutral densities = p/(kB*Tg); ion pressure neglected",
                         "flow": "pure O2 feed; common unknown neutral pumping rate; charged pumping zero",
                         "negative_ion_wall_loss_s": 0.0,
                         "hL_hR": "explicit inputs; source electronegative edge closure not solved",
                         "surface": "explicit gammaO (also O_1D), gammaMeta; source gammaO .17+/- .02 measured on stainless steel at 300K; gammaMeta .007 measured on Fe and adopted in the paper's SUS case; neither is a Si coefficient",
                         "zero_elastic": all(nu == 0 for nu in inputs.electron_momentum_nu_s.values())},
            "unresolved_energy_channels": [
                "Excited-target ionization k17/k19 has no additional reaction-resolved energy term in the reduced source budget.",
                "Electron detachment k7, superelastic k21, attachment k3/k20 and additional dissociation k22/k43 lack a full supplied energy-transfer table.",
                "New electrons from heavy-particle detachment do not have a supplied birth-energy distribution."],
            "individual_reaction_domains": {
                "k20": {"temperature_ev": [1.0, 4.5], "endpoints": "exclusive",
                        "source": data["reference"]["full_text_url"], "location": "section 3.4, p.1107"}},
            "solver_temperature_domain_ev": [1.0, 4.5],
            "general_table_temperature_domain_ev": [1.0, 7.0],
            "no_parameter_calibration": True, "literature_reproduction_success": False}


def solve_oxygen_case(inputs: StudyInputs | Mapping[str, Any], *, max_nfev: int = 2500,
                      multistarts: int = 3, tolerance: float = 1e-6,
                      initial_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return JSON-compatible solution and diagnostics, including failed cases.

    An optional converged result provides a continuation seed, followed by the
    requested physical multistarts if needed. Optimizer termination is not the
    acceptance criterion: every un-floored conservation diagnostic must pass.
    """
    if not isinstance(inputs, StudyInputs):
        inputs = StudyInputs(**dict(inputs))
    if max_nfev < 1 or multistarts < 1 or not math.isfinite(tolerance) or not 0 < tolerance < .1:
        raise ValueError("Require max_nfev>=1, multistarts>=1 and 0<tolerance<0.1")
    model = _CaseModel(inputs)
    seeds = []
    if initial_result is not None and initial_result.get("success"):
        n = initial_result["densities_m3"]
        positive_fraction = n["O+"] / (n["O+"] + n["O2+"])
        seeds.append(np.clip(np.asarray(
            [math.log(n[name] / model.neutral_reference) for name in NEUTRAL_NAMES + NEGATIVE_NAMES] +
            [math.log(n["e"] / model.neutral_reference), math.log(positive_fraction / (1 - positive_fraction)),
             math.log(initial_result["temperature_ev"]),
             math.log(initial_result["pumping_rate_s"] / model.pump_reference)]),
            model.lower + 1e-7, model.upper - 1e-7))
    seeds.extend(model.seed(i) for i in range(multistarts))
    attempts, best = [], None
    for seed in seeds:
        fit = least_squares(model.residual, seed, bounds=(model.lower, model.upper),
                            max_nfev=max_nfev, ftol=1e-10, xtol=1e-10, gtol=1e-10,
                            x_scale="jac")
        state = model.evaluate(fit.x)
        diagnostics = model.diagnostics(state, fit.x, tolerance)
        success = all(diagnostics["checks"].values())
        attempts.append({"nfev": int(fit.nfev), "optimizer_success": bool(fit.success),
                         "optimizer_status": int(fit.status), "max_relative": diagnostics["max_relative"],
                         "numerical_success": success})
        candidate = (success, -diagnostics["max_relative"], state, diagnostics, fit)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
        if success:
            break
    assert best is not None
    success, _, state, diagnostics, fit = best
    density = state["densities"]
    result = {"success": bool(success), "status": "converged_numerically" if success else "failed",
              "source_domain_valid": diagnostics["checks"]["source_reaction_domain"],
              "inputs": asdict(inputs), "temperature_ev": float(state["te"]),
              "electron_density_m3": float(density[10]), "densities_m3": dict(zip(ALL_NAMES, map(float, density))),
              "electronegativity": float(np.sum(density[5:8]) / density[10]),
              "pumping_rate_s": float(state["pump"]), "residence_time_s": float(1 / state["pump"]),
              "feed_molecules_s": model.feed_s, "volume_m3": model.volume,
              "neutral_density_target_m3": model.neutral_reference,
              "plasma_potential_v": float(state["phi"]), "loss_w": state["loss_w"],
              "residuals": diagnostics,
              "particle_production_loss_m3_s": {
                  name: {"production": float(state["production"][i]), "loss": float(state["loss"][i]),
                         "net": float(state["production"][i] - state["loss"][i])}
                  for i, name in enumerate(ALL_NAMES)},
              "reaction_event_rates_m3_s": {row["id"]: float(event) for row, event in zip(model.chemistry["rows"], state["events"])},
              "wall_rates_s": dict(zip(("k50", "k51", "k52", "k53", "k54"), map(float, state["wall_rates"]))),
              "electron_wall_loss_m3_s": state["electron_wall_m3_s"],
              "optimizer": {"method": "bounded log least_squares with physical multistarts",
                            "attempts": attempts, "message": str(fit.message),
                            "total_nfev": sum(a["nfev"] for a in attempts)},
              "metadata": study_metadata(inputs)}
    result["per_reaction_range_warnings"] = [] if result["source_domain_valid"] else [
        {"reaction": "k20", "temperature_ev": float(state["te"]),
         "valid_temperature_ev": [1.0, 4.5], "endpoints": "exclusive",
         "reason": "strict source reaction limit reached; candidate is not accepted"}]
    if not success:
        result["failure_reasons"] = [name for name, passed in diagnostics["checks"].items() if not passed]
    return result


def flatten_study_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """One CSV-compatible row; detailed source metadata stays in the JSON."""
    inputs = result["inputs"]
    row = {k: v for k, v in inputs.items() if k != "electron_momentum_nu_s"}
    row.update({f"electron_momentum_nu_{name}_s": value for name, value in inputs["electron_momentum_nu_s"].items()})
    row.update({name: result[name] for name in ("success", "status", "source_domain_valid", "temperature_ev", "electron_density_m3",
                                               "electronegativity", "pumping_rate_s", "residence_time_s", "plasma_potential_v")})
    row.update({f"density_{name}_m3": value for name, value in result["densities_m3"].items()})
    row.update({f"loss_{name}_w": value for name, value in result["loss_w"].items()})
    row.update({f"residual_{name}": value for name, value in result["residuals"].items()
                if isinstance(value, (int, float))})
    row["failed_checks"] = ",".join(result.get("failure_reasons", []))
    row["total_nfev"] = result["optimizer"]["total_nfev"]
    return row

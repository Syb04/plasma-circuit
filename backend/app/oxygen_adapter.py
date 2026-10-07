"""Web adapter for the documented, reduced oxygen particle/energy closure.

Prescribed power is TOTAL plasma absorbed power. In RF mode the circuit's
electron heating and sheath ion acceleration are distinct inputs. Conductive
sheath work closes the escaping-electron electrical work; RF ion acceleration
replaces floating-DC acceleration. The Bohm entrance energy remains in the
electron/ambipolar energy budget.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import math
from typing import Any, Callable, Mapping

import numpy as np
from scipy.optimize import least_squares

from .oxygen_study import ALL_NAMES, StudyInputs, _CaseModel, solve_oxygen_case, study_metadata
from .plasma_models import ELEMENTARY_CHARGE as E


def rf_power_budget(summary: Mapping[str, Any]) -> dict[str, float | str | None]:
    """Map the circuit ledger without granting free retarding-sheath work.

    Pe includes thermalized secondary acceleration if enabled. Conductive
    sheath power contains ion acceleration + secondary acceleration minus
    collected-electron retarding work. The secondary transfer is internal and
    is subtracted once from Pe+Pconductive when computing external absorption.
    """
    pe, pi = float(summary["electron_heating_w"]), float(summary["ion_acceleration_power_w"])
    secondary = float(summary.get("secondary_electron_acceleration_power_w", summary.get("electron_secondary_w", 0)))
    port = summary.get("electrode_absorbed_power_w")
    if port is not None and not math.isfinite(float(port)):
        raise ValueError("RF terminal port power must be finite")
    if "conductive_sheath_power_w" in summary:
        conductive = float(summary["conductive_sheath_power_w"])
        source = "resolved conductive RF sheath power"
    elif port is not None:
        conductive = float(port)-(pe-secondary)
        source = "inferred from terminal RF port balance"
    else:
        conductive = pi+secondary
        source = "RF callable supplied no conductive/port ledger; zero retarding-work approximation"
    values = (pe, pi, secondary, conductive)
    if not all(math.isfinite(v) for v in values) or min(pe, pi, secondary) < 0:
        raise ValueError("RF electron/ion/secondary powers must be finite nonnegative; conductive power finite")
    total = pe+conductive-secondary
    return {"electron_heating_w": pe, "ion_acceleration_w": pi, "secondary_acceleration_w": secondary,
        "conductive_sheath_w": conductive, "electron_retarding_sheath_work_w": pi+secondary-conductive,
        "total_absorbed_w": total, "electron_plus_ion_channel_sum_w": pe+pi,
        "terminal_port_minus_mapped_total_w": None if port is None else float(port)-total,
        "ledger_source": source}


def rf_secondary_yield(settings: Mapping[str, Any]) -> float:
    """Flux-weighted yield for equal ion current densities in two RF sheaths."""
    if "_rf_secondary_yield_override" in settings:
        override = float(settings["_rf_secondary_yield_override"])
        if not math.isfinite(override) or not 0 <= override <= 1:
            raise ValueError("Actual stamped RF secondary yield must be in [0,1]")
        return override
    surfaces = settings.get("surface_parameters", {})
    cathode = float(surfaces.get("cathode", {}).get("secondary_electron_yield",
        settings.get("secondary_electron_yield_cathode", 0)))
    anode = float(surfaces.get("anode", {}).get("secondary_electron_yield",
        settings.get("secondary_electron_yield_anode", 0)))
    ratio = float(settings.get("area_ratio", 5))
    if not all(math.isfinite(v) for v in (cathode, anode, ratio)) or not 0 <= cathode <= 1 or not 0 <= anode <= 1 or ratio <= 0:
        raise ValueError("RF secondary yields must be in [0,1] and area_ratio positive")
    return (cathode+ratio*anode)/(1+ratio)


def oxygen_inputs(settings: Mapping[str, Any], absorbed_power_w: float) -> StudyInputs:
    """Resolve exposed assumptions; defaults are source candidates, not Si data."""
    derived = settings.get("transport_mode", "explicit_h") == "gudmundsson_2000"
    gamma_o, gamma_meta = settings.get("gamma_o", .17), settings.get("gamma_meta", .007)
    if settings.get("surface_parameters"):
        surface = oxygen_surface_report(settings)
        effective = surface["effective_boundary_parameters"]
        gamma_o = effective["gamma_o"]
        gamma_meta = effective["gamma_metastable"]["O2(a1Delta)"]
    return StudyInputs(
        absorbed_power_w=absorbed_power_w,
        pressure_pa=settings.get("pressure_pa", 1.333223684),
        axial_edge_factor=None if derived else settings.get("axial_edge_factor", settings.get("wall_edge_factor", .5)),
        radial_edge_factor=None if derived else settings.get("radial_edge_factor", settings.get("wall_edge_factor", .5)),
        diffusion_o_m2_s=settings.get("diffusion_o_m2_s", 1.2),
        diffusion_o2_m2_s=settings.get("diffusion_o2_m2_s", .84),
        electron_momentum_nu_s=settings.get("electron_momentum_nu_s", {"O": 1e7, "O2": 1e7}),
        gas_temperature_k=settings.get("gas_temperature_k", 300),
        radius_m=settings.get("radius_m", float(settings.get("cathode_diameter_m", .3))/2),
        length_m=settings.get("length_m", settings.get("gap_m", .05)),
        flow_sccm=settings.get("flow_sccm", 50), gamma_o=gamma_o,
        gamma_meta=gamma_meta, transport_mode=settings.get("transport_mode", "explicit_h"),
        ion_temperature_k=settings.get("ion_temperature_k"),
        **({"ion_momentum_cross_sections_m2": settings["ion_momentum_cross_sections_m2"]}
           if "ion_momentum_cross_sections_m2" in settings else {}))


def oxygen_surface_report(settings: Mapping[str, Any]) -> dict[str, Any]:
    from .surface_models import resolve_surface_model
    radius = float(settings.get("radius_m", float(settings.get("cathode_diameter_m", .3))/2))
    length = float(settings.get("length_m", settings.get("gap_m", .05)))
    end = math.pi*radius**2
    # Chemistry occupies the cylinder, with two end caps and its lateral wall.
    # RF effective return area ratio is a separate lumped circuit input.
    areas = settings.get("oxygen_surface_areas_m2", {"cathode": end, "anode": end, "wall": 2*math.pi*radius*length})
    return resolve_surface_model(settings.get("surface_parameters"), areas_m2=areas,
        plasma_volume_m3=math.pi*radius**2*length,
        neutral_temperature_k=float(settings.get("gas_temperature_k", 300)))


class _ImposedIonPowerModel(_CaseModel):
    def __init__(self, inputs: StudyInputs, ion_acceleration_power_w: float | None, settings: Mapping[str, Any] | None = None):
        super().__init__(inputs)
        self.settings = dict(settings or {})
        self.ion_power = None if ion_acceleration_power_w is None else float(ion_acceleration_power_w)
        if self.ion_power is not None and (not math.isfinite(self.ion_power) or not 0 <= self.ion_power < inputs.absorbed_power_w):
            raise ValueError("RF ion acceleration must be finite, nonnegative and below total absorbed power")

    def evaluate(self, x: np.ndarray) -> dict[str, Any]:
        state = super().evaluate(x)
        if self.settings.get("electron_transport", {}).get("mode") == "cross_section_eedf":
            from .electron_transport import resolve_electron_transport
            from .plasma_models import ATOMIC_MASS, ELECTRON_MASS
            report = resolve_electron_transport(self.settings["electron_transport"], electron_temperature_ev=state["te"],
                neutral_densities_m3={"O": state["densities"][2], "O2": state["densities"][0]},
                explicit_nu_s=self.inputs.electron_momentum_nu_s)
            state["electron_transport"] = report
            state["loss_w"]["elastic"] = state["densities"][-1]*state["te"]*E*self.volume*sum(
                3*ELECTRON_MASS/(self.chemistry["species"][name]["mass_amu"]*ATOMIC_MASS)*nu
                for name, nu in report["target_collision_frequencies_s"].items())
        flux = state["electron_wall_m3_s"] * self.volume * E
        # Effective phi reports energy/charge; it is not a floating-potential
        # prediction or an instantaneous RF sheath voltage.
        if self.ion_power is not None:
            state["phi"] = self.ion_power / flux
            state["loss_w"]["ion_wall"] = self.ion_power + .5*state["te"]*flux
            state["loss_w"]["electron_wall"] *= 1+rf_secondary_yield(self.settings)
        state["loss_w"]["total"] = math.fsum(v for k, v in state["loss_w"].items() if k != "total")
        return state


def solve_oxygen_absorbed(settings: Mapping[str, Any], power_w: float, *,
                          ion_acceleration_power_w: float | None = None,
                          initial_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Solve total power closure; optional RF ion power replaces DC acceleration."""
    inputs = oxygen_inputs(settings, power_w)
    max_nfev = int(settings.get("max_chemistry_evaluations", 2500))
    if not 1 <= max_nfev <= 10000:
        raise ValueError("max_chemistry_evaluations must be 1–10000")
    if ion_acceleration_power_w is None and settings.get("electron_transport", {}).get("mode") != "cross_section_eedf":
        solved = solve_oxygen_case(inputs, max_nfev=max_nfev, initial_result=initial_result)
        if settings.get("surface_parameters"):
            solved["metadata"]["surface_parameters"] = oxygen_surface_report(settings)
            solved["metadata"]["closures"]["surface"] = "User supplied cathode/anode/wall probabilities combined by area-weighting gamma/(2-gamma), then used in the existing diffusion-plus-surface resistance; O2(b) not an evolved species"
        return solved
    model = _ImposedIonPowerModel(inputs, ion_acceleration_power_w, settings)
    seeds = []
    if initial_result is not None and initial_result.get("success"):
        n = initial_result["densities_m3"]
        seeds.append(np.asarray([math.log(n[name]/model.neutral_reference) for name in ALL_NAMES[:8]] +
            [math.log(n["e"]/model.neutral_reference), math.log(n["O+"]/n["O2+"]),
             math.log(initial_result["temperature_ev"]), math.log(initial_result["pumping_rate_s"]/model.pump_reference)]))
    seeds.extend(model.seed(i) for i in range(3))
    best, attempts = None, []
    for seed in seeds:
        fit = least_squares(model.residual, np.clip(seed, model.lower+1e-7, model.upper-1e-7),
            bounds=(model.lower, model.upper), max_nfev=max_nfev,
            ftol=1e-10, xtol=1e-10, gtol=1e-10, x_scale="jac")
        state = model.evaluate(fit.x)
        diagnostics = model.diagnostics(state, fit.x, 1e-6)
        success = all(diagnostics["checks"].values())
        attempts.append({"nfev": int(fit.nfev), "numerical_success": success})
        candidate = (success, -diagnostics["max_relative"], state, diagnostics)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
        if success:
            break
    assert best is not None
    success, _, state, diagnostics = best
    metadata = study_metadata(inputs)
    if ion_acceleration_power_w is not None:
        metadata["closures"].update({
            "absorbed_power": "RF electron heating + conductive sheath power - secondary acceleration transfer; electron retarding work closes the ion/electron sheath exchange",
            "wall_energy": "2Te*(1+flux-weighted secondary yield) collected thermal electron escape + 0.5Te Bohm entrance + imposed RF sheath ion acceleration, each once; secondaries assumed thermalized",
            "sheath": "RF effective ion energy per charge = imposed RF ion power / ion wall current; replaces floating-DC phi"})
        metadata["rf_secondary_electron_yield"] = rf_secondary_yield(settings)
    if "electron_transport" in state:
        metadata["electron_transport"] = state["electron_transport"]
        metadata["closures"]["elastic"] = "Target nu_i=n_i*<sigma_m*v> from supplied cross sections/EEDF, reevaluated at each density/Te; source-2001 elastic energy formula retained"
        metadata["chemistry_rate_distribution"] = "Published Maxwellian reaction fits at evolved Te; importing a transport EEDF does not replace the 48 chemistry rates"
    if settings.get("surface_parameters"):
        metadata["surface_parameters"] = oxygen_surface_report(settings)
        metadata["closures"]["surface"] = "User supplied cathode/anode/wall probabilities combined by area-weighting gamma/(2-gamma); O2(b) not an evolved species"
    return {"success": success, "status": "converged_numerically" if success else "failed",
        "inputs": asdict(inputs), "source_domain_valid": diagnostics["checks"]["source_reaction_domain"],
        "transport_domain_valid": state["transport"]["domain_valid"], "transport": state["transport"],
        "temperature_ev": state["te"], "electron_density_m3": float(state["densities"][-1]),
        "densities_m3": dict(zip(ALL_NAMES, map(float, state["densities"]))),
        "electronegativity": float(sum(state["densities"][5:8])/state["densities"][-1]),
        "pumping_rate_s": state["pump"], "residence_time_s": 1/state["pump"],
        "plasma_potential_v": state["phi"], "volume_m3": model.volume,
        "loss_w": state["loss_w"], "wall_rates_s": dict(zip(("k50", "k51", "k52", "k53", "k54"), map(float, state["wall_rates"]))),
        "electron_wall_loss_m3_s": state["electron_wall_m3_s"], "residuals": diagnostics,
        "optimizer": {"method": "bounded oxygen balances with imposed RF ion acceleration", "attempts": attempts},
        "metadata": metadata, "failure_reasons": [name for name, ok in diagnostics["checks"].items() if not ok]}


def oxygen_tables(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"name": "O₂ species / 粒子密度", "columns": ["species", "density_m3"],
         "rows": [[name, n] for name, n in result["densities_m3"].items()]},
        {"name": "Reduced energy budget / 縮約エネルギー収支", "columns": ["channel", "power_w"],
         "rows": [[name, value] for name, value in result["loss_w"].items()]},
        {"name": "O₂ wall transport / 壁輸送", "columns": ["reaction", "rate_s^-1"],
         "rows": [[name, value] for name, value in result["wall_rates_s"].items()]}]


def oxygen_rf_settings(settings: Mapping[str, Any], chemistry: Mapping[str, Any]):
    """Match circuit wall current to the multi-species chemistry ion loss."""
    from .plasma import CCPSettings
    n, kw = chemistry["densities_m3"], chemistry["wall_rates_s"]
    ion_events = {"O+": n["O+"]*kw["k50"], "O2+": n["O2+"]*kw["k51"]}
    flux = sum(ion_events.values())
    mass = (15.999*ion_events["O+"]+31.998*ion_events["O2+"])/flux
    collision_nu = sum(chemistry.get("metadata", {}).get("electron_transport", {}).get("target_collision_frequencies_s",
        settings.get("electron_momentum_nu_s", {"O": 1e7, "O2": 1e7})).values())
    parsed = CCPSettings.parse(dict(settings) | {"gas": "O2", "electron_density_m3": n["e"],
        "electron_temperature_ev": chemistry["temperature_ev"], "electronegativity": chemistry["electronegativity"],
        "ion_mass_amu": mass, "plasma_volume_m3": chemistry["volume_m3"],
        "momentum_collision_frequency_hz": settings.get("momentum_collision_frequency_hz", collision_nu)})
    density_current = E*chemistry["volume_m3"]*flux/(parsed.cathode_area+parsed.anode_area)
    if "ion_wall_current_density_a_m2" in parsed.__dataclass_fields__:
        parsed = replace(parsed, ion_wall_current_density_a_m2=density_current)
    if "neutral_species_densities_m3" in parsed.__dataclass_fields__:
        parsed = replace(parsed, neutral_species_densities_m3={"O": n["O"], "O2": n["O2"]})
    return parsed


def execute_oxygen(settings: dict[str, Any], rf_solver: Callable | None = None) -> dict[str, Any]:
    """Execute the O2 global adapter, returning the standard web result shape."""
    mode = settings.get("power_mode", "rf_coupled")
    if mode not in {"prescribed_absorbed", "rf_coupled"}:
        raise ValueError("O₂ power_mode must be prescribed_absorbed or rf_coupled")
    if mode == "prescribed_absorbed":
        if "absorbed_power_w" not in settings:
            raise ValueError("Prescribed O₂ mode requires total absorbed_power_w")
        chemistry = solve_oxygen_absorbed(settings, float(settings["absorbed_power_w"]))
        result = {"kind": "global", "converged": chemistry["success"], "summary": {},
            "axis": {"name": "steady state", "unit": "", "values": []}, "signals": [],
            "tables": [], "logs": [], "netlist": "", "solver": {"global": "scipy.optimize.least_squares"},
            "model_metadata": {}, "diagnostics": {}}
        history = []
    else:
        from .plasma import CCPSettings, effective_rf_settings, solve_ccp
        rf_solver = rf_solver or solve_ccp
        parsed = CCPSettings.parse(dict(settings) | {"gas": "O2"})
        if not 1 < parsed.electron_temperature_ev < 4.5:
            raise ValueError("O₂ k20 requires initial 1 < Te < 4.5 eV")
        history, chemistry, result = [], None, None
        limit = int(settings.get("max_global_iterations", 18))
        if not 3 <= limit <= 40:
            raise ValueError("max_global_iterations must be 3–40")
        for iteration in range(limit):
            result = rf_solver(parsed)
            actual = effective_rf_settings(parsed, result)
            actual_yield = (actual.secondary_electron_yield_cathode*actual.cathode_area+
                actual.secondary_electron_yield_anode*actual.anode_area)/(actual.cathode_area+actual.anode_area)
            case_settings = settings | {"_rf_secondary_yield_override": actual_yield}
            target_nu = result.get("model_metadata", {}).get("electron_transport", {}).get("target_collision_frequencies_s", {})
            if set(target_nu) == {"O", "O2"} and settings.get("electron_transport", {}).get("mode") != "cross_section_eedf":
                case_settings["electron_momentum_nu_s"] = target_nu
            summary = result["summary"]
            budget = rf_power_budget(summary)
            pe, pi, absorbed = budget["electron_heating_w"], budget["ion_acceleration_w"], budget["total_absorbed_w"]
            if pe <= 0 or absorbed <= pi:
                raise RuntimeError("RF total absorption does not cover ion-wall acceleration and positive collisional/electron losses; this reduced steady oxygen budget has no admissible state at the trial conditions")
            next_chemistry = solve_oxygen_absorbed(case_settings, absorbed, ion_acceleration_power_w=pi,
                                                   initial_result=chemistry)
            density_error = abs(math.log(next_chemistry["electron_density_m3"]/parsed.electron_density_m3))
            temperature_error = abs(math.log(next_chemistry["temperature_ev"]/parsed.electron_temperature_ev))
            current_target = oxygen_rf_settings(settings, next_chemistry)
            current_error = 0.0
            if getattr(parsed, "ion_wall_current_density_a_m2", None) is not None:
                current_error = abs(math.log(current_target.ion_wall_current_density_a_m2/parsed.ion_wall_current_density_a_m2))
            alpha_error = abs(math.log((1+current_target.electronegativity)/(1+parsed.electronegativity)))
            mass_error = abs(math.log(current_target.ion_mass_amu/parsed.ion_mass_amu))
            history.append({"iteration": iteration+1, "electron_heating_w": pe, "ion_acceleration_power_w": pi,
                "total_plasma_absorbed_power_w": absorbed, "electron_retarding_sheath_work_w": budget["electron_retarding_sheath_work_w"],
                "density_log_error": density_error,
                "temperature_log_error": temperature_error, "wall_current_log_error": current_error,
                "electronegativity_log_error": alpha_error, "ion_mass_log_error": mass_error})
            chemistry = next_chemistry
            if not chemistry["success"]:
                break
            if max(density_error, temperature_error, current_error, alpha_error, mass_error) < .005:
                break
            parsed = current_target
        assert result is not None and chemistry is not None
        result["converged"] = bool(result.get("converged") and chemistry["success"] and
            max(density_error, temperature_error, current_error, alpha_error, mass_error) < .005)
        result["model_metadata"]["rf_chemistry_coupling"] = {
            "ion_mass": "wall-event-flux weighted effective O+/O2+ mass; single effective matrix sheath",
            "ion_wall_current": "chemistry ion wall loss * volume * e / sum of effective circuit cathode+anode areas; cylinder wall losses mapped to these two equivalent sheaths",
            "convergence_log_tolerance": .005,
            "total_power_definition": "electron_heating_w + conductive_sheath_power_w - secondary_electron_acceleration_power_w; escaping-electron retarding work closes Pe/Pi, floating ion phi is replaced",
            "electrode_power": "Retained circuit port diagnostic; not substituted for electron heating"}
        result["diagnostics"]["rf_oxygen_power_budget"] = budget
    result["kind"] = "global"
    result["summary"].update({"electron_density_m3": chemistry["electron_density_m3"],
        "electron_temperature_ev": chemistry["temperature_ev"], "electronegativity": chemistry["electronegativity"],
        "total_plasma_absorbed_power_w": chemistry["inputs"]["absorbed_power_w"],
        "reduced_energy_loss_w": chemistry["loss_w"]["total"],
        "gas_temperature_k": chemistry["inputs"]["gas_temperature_k"],
        "residence_time_s": chemistry["residence_time_s"]})
    result["tables"].extend(oxygen_tables(chemistry))
    result["diagnostics"].update({"oxygen_balances": chemistry["residuals"], "oxygen_solution": chemistry,
                                  "global_iterations": history, "power_mode": mode})
    result["model_metadata"]["chemistry"] = chemistry["metadata"]
    result["logs"].append("O₂: 48 particle reactions, 16 excitation terms, flow and wall closure; reduced energies only. " +
        ("Explicit user surface assumptions applied; no material coefficient inferred." if settings.get("surface_parameters") else
         "Default surface candidates are SUS/Fe, not validated Si coefficients."))
    if not chemistry["success"]:
        result["logs"].append("O₂ numerical balances failed: " + ", ".join(chemistry.get("failure_reasons", [])))
    return result

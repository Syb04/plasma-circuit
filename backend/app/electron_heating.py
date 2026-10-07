"""Drude, secondary-electron, and optional moving-sheath electron heating.

The reduced moving-wall model is an explicitly selectable estimate from actual
sheath movement. Its power is accounted separately from the RF circuit ledger;
capacitor V*dQ/dt is never counted as dissipative electron heating.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
from scipy.special import ndtr

from .plasma_models import ELEMENTARY_CHARGE, ELECTRON_MASS, EPSILON_0

HEATING_VERSION = "moving-maxwellian-reflecting-sheath-0.1"


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'} and finite")
    return result


def _waveform(value: Any, time: np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != time.shape or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite waveform matching time_s")
    return array


def _mean(time: np.ndarray, value: np.ndarray) -> float:
    return float(np.trapz(value, time) / (time[-1] - time[0]))


def moving_wall_power_flux(velocity_m_s: Any, *, electron_density_m3: float,
                           electron_temperature_ev: float, reflection_probability: float = 1.0) -> dict[str, np.ndarray]:
    """Specular reflection of an incoming 1D Maxwellian, in W/m2.

    u>0 is sheath expansion into the plasma. Electrons with normal lab velocity
    v<u encounter the wall at rate (u-v)f(v); reflection gives dE=2me*u*(u-v).
    Subtract p_e*u, the reversible constant-pressure boundary work. The
    remaining flux is nonnegative, including during collapse, and integrates
    to total work on a periodic constant-density/temperature boundary.
    """
    density = _number(electron_density_m3, "electron_density_m3", positive=True)
    temperature = _number(electron_temperature_ev, "electron_temperature_ev", positive=True)
    reflection = _number(reflection_probability, "reflection_probability")
    if reflection > 1:
        raise ValueError("reflection_probability must be in [0,1]")
    u = np.asarray(velocity_m_s, dtype=float)
    if not np.all(np.isfinite(u)):
        raise ValueError("Sheath velocities must be finite")
    thermal = math.sqrt(ELEMENTARY_CHARGE * temperature / ELECTRON_MASS)
    z = u / thermal
    gaussian = np.exp(-z**2 / 2) / math.sqrt(2 * math.pi)
    # Difference form avoids subtracting two large nearly equal pressures at
    # u/vthermal -> 0. Phi(z)-1/2 = erf(z/sqrt(2))/2.
    from scipy.special import erf
    moment_difference = u**2 * ndtr(z) + thermal**2 * .5 * erf(z / math.sqrt(2)) + u * thermal * gaussian
    irreversible = 2 * ELECTRON_MASS * density * reflection * u * moment_difference
    reversible = density * ELEMENTARY_CHARGE * temperature * reflection * u
    return {"irreversible_w_m2": irreversible, "reversible_pressure_work_w_m2": reversible,
            "total_reflection_work_w_m2": irreversible + reversible}


def resolve_electron_heating(config: Mapping[str, Any] | None, *, time_s: Any, bulk_current_a: Any,
                             bulk_resistance_ohm: float, sheath_voltage_v: Mapping[str, Any],
                             sheath_areas_m2: Mapping[str, float], electron_density_m3: float,
                             electron_temperature_ev: float, sheath_ion_density_m3: float,
                             rf_available_power_w: float | None = None,
                             electrode_absorbed_power_w: float | None = None,
                             conductive_sheath_power_w: float | None = None,
                             secondary_electron_acceleration_power_w: float = 0.0,
                             circuit_sheath_heating_power_w: float | None = None,
                             circuit_sheath_resistor_power_w: float = 0.0,
                             sheath_position_m: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate heating on the retained RF time mesh and expose its ledger.

    ``sheath_voltage_v`` is the positive plasma-minus-metal drop. A direct
    position waveform is preferred if supplied; otherwise s=sqrt(2 eps V/e ni)
    is the same cold uniform-ion matrix closure as the RF template. Supplemental
    heating is never silently scaled to fit the circuit's available power.
    """
    config = config or {}
    mode = config.get("mode", "bulk_drude")
    if mode not in {"bulk_drude", "moving_wall_maxwellian"}:
        raise ValueError("electron_heating.mode must be bulk_drude or moving_wall_maxwellian")
    time = np.asarray(time_s, dtype=float)
    if time.ndim != 1 or len(time) < 3 or not np.all(np.isfinite(time)) or np.any(np.diff(time) <= 0):
        raise ValueError("time_s needs at least three strictly increasing finite times")
    current = _waveform(bulk_current_a, time, "bulk_current_a")
    resistance = _number(bulk_resistance_ohm, "bulk_resistance_ohm")
    secondary = _number(secondary_electron_acceleration_power_w, "secondary_electron_acceleration_power_w")
    configured_resistor = _number(circuit_sheath_resistor_power_w, "circuit_sheath_resistor_power_w")
    bulk_wave = resistance * current**2
    bulk = _mean(time, bulk_wave)
    reflection = _number(config.get("reflection_probability", 1.0), "reflection_probability")
    if reflection > 1:
        raise ValueError("reflection_probability must be in [0,1]")
    wall_wave = np.zeros_like(time)
    reversible_wave = np.zeros_like(time)
    reversible_exact_mean = 0.0
    sheath_reports: dict[str, Any] = {}
    domain_warnings: list[str] = []
    domain_valid = True
    if mode == "moving_wall_maxwellian":
        if not sheath_areas_m2 or set(sheath_voltage_v) != set(sheath_areas_m2):
            raise ValueError("Sheath voltage and area roles must match and be nonempty")
        ion_density = _number(sheath_ion_density_m3, "sheath_ion_density_m3", positive=True)
        if sheath_position_m is not None and set(sheath_position_m) != set(sheath_areas_m2):
            raise ValueError("Direct sheath_position_m roles must match sheath areas")
        thermal = math.sqrt(ELEMENTARY_CHARGE * _number(electron_temperature_ev, "electron_temperature_ev", positive=True) / ELECTRON_MASS)
        for role, area_input in sheath_areas_m2.items():
            area = _number(area_input, f"{role} sheath area", positive=True)
            voltage = _waveform(sheath_voltage_v[role], time, f"{role} sheath voltage")
            if np.any(voltage < -.2):
                domain_valid = False
                domain_warnings.append(f"{role}: plasma-minus-metal voltage becomes negative; an electron-attracting sheath is outside this reflecting-ion-sheath model.")
            if sheath_position_m is None:
                position = np.sqrt(2 * EPSILON_0 * np.maximum(voltage, 0) / (ELEMENTARY_CHARGE * ion_density))
                movement_source = "Matrix-ion s(t)=sqrt(2*eps0*max(Vplasma-Vmetal,0)/(e*ni)); no smoothing fit"
            else:
                position = _waveform(sheath_position_m[role], time, f"{role} sheath position")
                if np.any(position < 0):
                    raise ValueError("Sheath positions must be nonnegative")
                movement_source = "Supplied actual sheath boundary position waveform"
            velocity = np.gradient(position, time, edge_order=2)
            flux = moving_wall_power_flux(velocity, electron_density_m3=electron_density_m3,
                                          electron_temperature_ev=electron_temperature_ev,
                                          reflection_probability=reflection)
            irreversible = area * flux["irreversible_w_m2"]
            reversible = area * flux["reversible_pressure_work_w_m2"]
            # Constant-pressure work has an exact endpoint integral. Preserve
            # it instead of reporting numerical derivative drift as heating.
            exact_pressure_mean = (area * float(electron_density_m3) * ELEMENTARY_CHARGE
                                   * float(electron_temperature_ev) * reflection
                                   * float(position[-1] - position[0]) / (time[-1] - time[0]))
            reversible_exact_mean += exact_pressure_mean
            wall_wave += irreversible
            reversible_wave += reversible
            excursion = float(np.max(position) - np.min(position))
            periodic_error = abs(float(position[-1] - position[0])) / max(excursion, 1e-30)
            if periodic_error > .02:
                domain_valid = False
                domain_warnings.append(f"{role}: retained sheath motion is not periodic within 2%; mean reversible pressure work cannot be ignored.")
            max_ratio = float(np.max(np.abs(velocity)) / thermal)
            if max_ratio > .3:
                domain_warnings.append(f"{role}: max sheath speed/thermal normal speed={max_ratio:.3g}; the incoming Maxwellian reservoir assumption requires kinetic validation.")
            sheath_reports[role] = {"area_m2": area, "movement_source": movement_source,
                                   "mean_irreversible_heating_w": _mean(time, irreversible),
                                   "mean_reversible_pressure_work_w": exact_pressure_mean,
                                   "numerical_pressure_waveform_mean_w": _mean(time, reversible),
                                   "mean_total_reflection_work_w": _mean(time, irreversible) + exact_pressure_mean,
                                   "max_speed_over_thermal_normal_speed": max_ratio,
                                   "position_periodicity_relative_error": periodic_error,
                                   "position_m": position.tolist(), "velocity_m_s": velocity.tolist(),
                                   "irreversible_heating_w": irreversible.tolist()}
    estimate = _mean(time, wall_wave)
    backreaction = config.get("effective_circuit_backreaction", False)
    if not isinstance(backreaction, bool):
        raise ValueError("effective_circuit_backreaction must be a boolean")
    if circuit_sheath_heating_power_w is not None:
        if not backreaction or mode != "moving_wall_maxwellian":
            raise ValueError("Measured circuit sheath heating requires moving_wall_maxwellian and effective_circuit_backreaction=true")
        moving = _number(circuit_sheath_heating_power_w, "circuit_sheath_heating_power_w")
    elif backreaction:
        raise ValueError("effective_circuit_backreaction requires the measured circuit_sheath_heating_power_w")
    else:
        moving = estimate
    reversible = reversible_exact_mean
    electron = bulk + moving + secondary + configured_resistor
    ledger: dict[str, Any] = {"bulk_drude_w": bulk, "moving_wall_irreversible_w": moving,
                              "moving_wall_estimated_w": estimate,
                              "circuit_minus_estimated_sheath_heating_w": moving - estimate if backreaction else None,
                              "secondary_electron_acceleration_w": secondary,
                              "configured_circuit_sheath_resistor_w": configured_resistor,
                              "electron_heating_w": electron,
                              "reversible_pressure_work_w": reversible,
                              "sheath_capacitor_vi_included": False,
                              "rf_backreaction_included": backreaction}
    budget_valid: bool | None = None
    tolerance = _number(config.get("budget_relative_tolerance", .01), "budget_relative_tolerance", positive=True)
    if tolerance > .1:
        raise ValueError("budget_relative_tolerance must not exceed 0.1")
    if rf_available_power_w is not None:
        available = float(rf_available_power_w)
        if not math.isfinite(available):
            raise ValueError("rf_available_power_w must be finite")
        residual = available - electron
        budget_valid = electron <= available + tolerance * max(abs(available), electron, 1e-20)
        ledger.update({"electron_available_rf_power_w": available, "electron_available_budget_residual_w": residual})
    if electrode_absorbed_power_w is not None or conductive_sheath_power_w is not None:
        if electrode_absorbed_power_w is None or conductive_sheath_power_w is None:
            raise ValueError("Supply both electrode_absorbed_power_w and conductive_sheath_power_w for the RF ledger")
        port, conduction = float(electrode_absorbed_power_w), float(conductive_sheath_power_w)
        if not math.isfinite(port) or not math.isfinite(conduction):
            raise ValueError("RF power ledger values must be finite")
        original_residual = port - bulk - conduction - configured_resistor - (moving if backreaction else 0)
        remaining_sheath = conduction - secondary
        accounted = electron + remaining_sheath
        residual = port - accounted
        relative = abs(residual) / max(abs(port), abs(accounted), 1e-20)
        rf_valid = relative <= tolerance
        budget_valid = rf_valid if budget_valid is None else budget_valid and rf_valid
        ledger.update({"electrode_absorbed_power_w": port, "original_conductive_sheath_power_w": conduction,
                       "original_rf_power_residual_w": original_residual,
                       "remaining_conductive_sheath_channel_w": remaining_sheath,
                       "extended_accounted_power_w": accounted, "extended_rf_power_residual_w": residual,
                       "extended_rf_power_relative_error": relative,
                       "secondary_channel_definition": "Secondary acceleration transferred out of the stamped conductive-sheath power channel, counted once."})
    if budget_valid is False:
        domain_warnings.append("Electron heating fails the supplied RF power budget. No power was rescaled.")
    if backreaction:
        closure_error = abs(moving - estimate) / max(moving, estimate, 1e-20)
        ledger["circuit_vs_estimated_sheath_relative_error"] = closure_error
        if closure_error > tolerance:
            domain_valid = False
            domain_warnings.append("The effective RF sheath resistance has not converged to the moving-wall estimate.")
    return {"mode": mode, "version": HEATING_VERSION, "electron_heating_w": electron,
            "electron_bulk_w": bulk, "electron_sheath_moving_wall_w": moving,
            "electron_sheath_moving_wall_estimated_w": estimate,
            "electron_sheath_configured_resistor_w": configured_resistor,
            "electron_secondary_w": secondary, "mean_reversible_pressure_work_w": reversible,
            "budget": ledger, "budget_valid": budget_valid, "domain_valid": domain_valid,
            "domain_warnings": domain_warnings, "sheaths": sheath_reports,
            "signals": [{"name": "P(electron_bulk)", "unit": "W", "values": bulk_wave.tolist()},
                        {"name": "P(electron_sheath_moving_wall)", "unit": "W", "values": wall_wave.tolist()},
                        {"name": "P(sheath_reversible_pressure_work)", "unit": "W", "values": reversible_wave.tolist()}],
            "assumptions": ["Planar specular reflection from the actual moving sheath boundary; constant isotropic Maxwellian incoming electron reservoir.",
                            "Boundary electron density and temperature are supplied assumptions; spatial kinetic depletion and phase mixing are unresolved.",
                            "Irreversible reflection work is separated from constant-pressure reversible boundary work before cycle averaging.",
                            "Moving-wall power is either a separate estimate with its budget mismatch exposed, or a measured effective RF resistance closed externally against that estimate.",
                            "Any independently configured measured sheath-resistor dissipation is retained as a separate electron-heating channel, counted once.",
                            "Secondary acceleration is an ion-induced yield channel supplied by the RF sheath model, with no avalanche or secondary EEDF solved."],
            "formula": "P/A = 2*me*ne*r*u*[(u**2+vt**2)*Phi(u/vt)+u*vt*phi(u/vt)-vt**2/2]; vt=sqrt(e*Te/me)",
            "status": "optional_reduced_model_requires_kinetic_validation" if mode == "moving_wall_maxwellian" else "Drude_resistive_energy_accounting"}

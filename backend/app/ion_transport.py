"""Test-particle IEDF in a prescribed, periodically driven planar sheath.

This is a trajectory calculation, not PIC or an instantaneous q*Vs histogram.
The field is uniform in a fixed slab; no particle changes the supplied field.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .plasma_models import ATOMIC_MASS, BOLTZMANN, ELEMENTARY_CHARGE, EPSILON_0


MODEL_VERSION = "prescribed-uniform-sheath-trajectories-0.1"


def _number(value: Any, name: str, *, positive: bool = True) -> float:
    result = float(value)
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'} and finite")
    return result


def _integer(value: Any, name: str, low: int, high: int) -> int:
    result = _number(value, name, positive=False)
    if isinstance(value, bool) or not result.is_integer() or not low <= result <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return int(result)


def _flight_time(distance: np.ndarray, speed: np.ndarray, acceleration: np.ndarray) -> np.ndarray:
    """First forward passage over distance, in the directed travel coordinate."""
    finite = np.isfinite(distance)
    safe_distance = np.where(finite, distance, 0.0)
    discriminant = speed * speed + 2 * acceleration * safe_distance
    root = np.sqrt(np.maximum(discriminant, 0))
    denominator = speed + root
    out = np.full_like(distance, np.inf)
    valid = finite & (discriminant >= 0) & (denominator > 0)
    out[valid] = 2 * safe_distance[valid] / denominator[valid]
    out[distance <= 1e-18] = 0
    return out


def _periodic_waveform(waveform: dict[str, Any], settings: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, float, dict[str, float]]:
    times = np.asarray(waveform.get("time_s", ()), dtype=float)
    volts = np.asarray(waveform.get("voltage_v", ()), dtype=float)
    if times.ndim != 1 or len(times) < 2 or times.shape != volts.shape:
        raise ValueError("IEDF waveform needs matching time_s and voltage_v arrays with at least two samples")
    if not np.all(np.isfinite(times)) or not np.all(np.isfinite(volts)) or np.any(np.diff(times) <= 0):
        raise ValueError("IEDF waveform times must be strictly increasing; all samples must be finite")
    frequency = _number(settings.get("frequency_hz", 1 / (times[-1] - times[0])), "frequency_hz")
    period = 1 / frequency
    if times[-1] - times[0] < period * (1 - 1e-8):
        raise ValueError("IEDF waveform must cover at least one complete RF period")
    # Keep the actual last-period knots and interpolate only its boundary.
    start = times[-1] - period
    inside = (times > start) & (times < times[-1])
    cycle_t = np.concatenate(([0.0], times[inside] - start, [period]))
    cycle_v = np.concatenate(([np.interp(start, times, volts)], volts[inside], [volts[-1]]))
    mismatch = abs(cycle_v[-1] - cycle_v[0]) / max(float(np.max(np.abs(cycle_v))), 1e-20)
    negative_fraction = float(np.trapz((cycle_v < 0).astype(float), cycle_t) / period)
    # Positive drop accelerates positive ions toward the electrode. A negative
    # drop is outside this ion-sheath approximation, and explicitly diagnosed.
    cycle_v = np.maximum(cycle_v, 0)
    mean_voltage = float(np.trapz(cycle_v, cycle_t) / period)
    return cycle_t, cycle_v, period, {"mean_accelerating_voltage_v": mean_voltage,
        "periodic_boundary_relative_mismatch": mismatch, "negative_sheath_drop_fraction": negative_fraction}


def _integrate_species(cycle_t: np.ndarray, cycle_v: np.ndarray, period: float,
                       thickness: float, species: dict[str, Any], settings: dict[str, Any],
                       neutral_density: float, gas_temperature: float, rng: np.random.Generator) -> dict[str, Any]:
    count = _integer(settings.get("particles_per_species", 512), "particles_per_species", 16, 20000)
    steps = _integer(settings.get("steps_per_rf_period", 256), "steps_per_rf_period", 16, 8192)
    transit_steps = _integer(settings.get("steps_per_transit", 64), "steps_per_transit", 8, 4096)
    max_periods = _number(settings.get("max_transit_periods", 30), "max_transit_periods")
    if max_periods > 1000:
        raise ValueError("max_transit_periods must be <= 1000")
    mass = _number(species["mass_amu"], "mass_amu") * ATOMIC_MASS
    density = _number(species["density_m3"], "density_m3", positive=False)
    charge = _integer(species.get("charge_number", 1), "charge_number", 1, 3)
    temperature = _number(settings.get("electron_temperature_ev", 3), "electron_temperature_ev")
    cross_section = _number(species.get("charge_exchange_cross_section_m2",
        settings.get("charge_exchange_cross_section_m2", 0)), "charge_exchange_cross_section_m2", positive=False)
    if charge != 1 and cross_section > 0:
        raise ValueError("The resonant charge-exchange approximation supports singly charged ions only")
    optical_depth = neutral_density * cross_section * thickness
    if optical_depth > 100:
        raise ValueError("IEDF sheath charge-exchange optical depth must be <= 100 for the bounded test-particle solver")
    bohm = math.sqrt(charge * ELEMENTARY_CHARGE * temperature / mass)
    max_voltage = float(np.max(cycle_v))
    mean_voltage = float(np.trapz(cycle_v, cycle_t) / period)
    dc_acceleration = charge * ELEMENTARY_CHARGE * mean_voltage / (mass * thickness)
    dc_transit = 2 * thickness / (bohm + math.sqrt(bohm**2 + 2 * dc_acceleration * thickness))
    dt = min(period / steps, dc_transit / transit_steps)
    max_time = max_periods * period
    # A long DC transit must be explicitly requested rather than hidden by
    # extending max_time; uncollected particles remain in diagnostics.
    max_steps = int(math.ceil(max_time / dt))
    if max_steps * count > 60_000_000:
        raise ValueError("IEDF trajectory budget exceeds 60 million particle time steps; reduce sampling or max_transit_periods")
    phase = (np.arange(count) + rng.random(count)) / count * period
    rng.shuffle(phase)
    launch = phase.copy()
    clock = phase.copy()
    x = np.zeros(count)
    velocity = np.full(count, bohm)
    status = np.zeros(count, dtype=int)  # 0 active, 1 electrode, 2 entrance, 3 event budget
    energies = np.zeros(count)
    transit = np.zeros(count)
    collisions = np.zeros(count, dtype=int)
    field_work = np.zeros(count)
    transferred_neutral_energy = np.zeros(count)
    thermal_entry_energy = np.zeros(count)
    mean_free_path = 1 / (neutral_density * cross_section) if optical_depth > 0 else None
    path = rng.exponential(mean_free_path, count) if mean_free_path is not None else np.full(count, np.inf)
    neutral_energies: list[float] = []
    neutral_transit: list[float] = []
    backwards_neutrals = 0
    event_budget_exhausted = 0
    thermal_sigma = math.sqrt(BOLTZMANN * gas_temperature / mass)
    event_limit = _integer(settings.get("max_collision_events_per_particle", 10000), "max_collision_events_per_particle", 1, 100000)
    elapsed = 0.0
    for _ in range(max_steps):
        if not np.any(status == 0):
            break
        remaining = np.where(status == 0, min(dt, max_time - elapsed), 0.0)
        for _events in range(128):
            indices = np.flatnonzero((status == 0) & (remaining > dt * 1e-12))
            if not len(indices):
                break
            h = remaining[indices]
            acceleration = charge * ELEMENTARY_CHARGE / (mass * thickness) * np.interp(
                np.mod(clock[indices] + h / 2, period), cycle_t, cycle_v)
            vel = velocity[indices]
            direction = np.where(vel < 0, -1.0, 1.0)
            turn = np.full(len(indices), np.inf)
            turning = (vel < 0) & (acceleration > 0)
            turn[turning] = -vel[turning] / acceleration[turning]
            segment = np.minimum(h, turn)
            boundary_distance = np.where(direction > 0, thickness - x[indices], x[indices])
            boundary_time = _flight_time(boundary_distance, np.abs(vel), direction * acceleration)
            collision_time = _flight_time(path[indices], np.abs(vel), direction * acceleration)
            advance = np.minimum(segment, np.minimum(boundary_time, collision_time))
            distance = vel * advance + 0.5 * acceleration * advance**2
            field_work[indices] += mass * acceleration * distance / ELEMENTARY_CHARGE
            x[indices] += distance
            velocity[indices] += acceleration * advance
            clock[indices] += advance
            remaining[indices] -= advance
            path[indices] -= np.abs(distance)
            at_boundary = (boundary_time <= segment) & (boundary_time <= collision_time)
            at_collision = (collision_time <= segment) & (collision_time < boundary_time)
            wall = indices[at_boundary & (direction > 0)]
            entrance = indices[at_boundary & (direction < 0)]
            status[wall], status[entrance] = 1, 2
            energies[wall] = 0.5 * mass * velocity[wall]**2 / ELEMENTARY_CHARGE
            transit[wall] = clock[wall] - launch[wall]
            cx = indices[at_collision]
            if len(cx):
                # Resonant equal-mass charge exchange: the old ion becomes a
                # ballistic neutral; a neutral sampled from Tg becomes the ion.
                forward = velocity[cx] > 0
                transferred_neutral_energy[cx] += 0.5 * mass * velocity[cx]**2 / ELEMENTARY_CHARGE
                outgoing = cx[forward]
                neutral_energies.extend((0.5 * mass * velocity[outgoing]**2 / ELEMENTARY_CHARGE).tolist())
                neutral_transit.extend(((thickness - x[outgoing]) / velocity[outgoing]).tolist())
                backwards_neutrals += int(np.count_nonzero(~forward))
                collisions[cx] += 1
                velocity[cx] = rng.normal(0, thermal_sigma, len(cx))
                thermal_entry_energy[cx] += 0.5 * mass * velocity[cx]**2 / ELEMENTARY_CHARGE
                path[cx] = rng.exponential(mean_free_path, len(cx))
                exceeded = cx[collisions[cx] >= event_limit]
                status[exceeded] = 3
                event_budget_exhausted += len(exceeded)
            reached_turn = (~at_boundary) & (~at_collision) & (turn <= h)
            velocity[indices[reached_turn]] = 0
        # Pathological collision sequences are preserved as unresolved counts.
        unfinished = (status == 0) & (remaining > dt * 1e-12)
        status[unfinished] = 3
        event_budget_exhausted += int(np.count_nonzero(unfinished))
        elapsed += dt
        if elapsed >= max_time * (1 - 1e-12):
            break
    arrived = status == 1
    uncollected = (status == 0) | (status == 3)
    ion_energies = energies[arrived]
    launch_flux = density * bohm
    final_kinetic_energy = 0.5 * mass * velocity**2 / ELEMENTARY_CHARGE
    energy_residual = float(np.sum(final_kinetic_energy + transferred_neutral_energy - field_work - thermal_entry_energy - charge * temperature / 2))
    energy_scale = float(np.sum(np.abs(field_work) + thermal_entry_energy + charge * temperature / 2))
    return {"name": str(species.get("name", "ion")), "mass_amu": mass / ATOMIC_MASS,
        "charge_number": charge, "density_m3": density, "bohm_speed_m_s": bohm,
        "entrance_energy_ev": charge * temperature / 2,
        "incident_energies_ev": ion_energies, "neutral_energies_ev": np.asarray(neutral_energies),
        "launch_flux_m2_s": launch_flux, "ion_flux_m2_s": float(launch_flux * np.count_nonzero(arrived) / count),
        "fast_neutral_flux_m2_s": launch_flux * len(neutral_energies) / count,
        "mean_energy_ev": float(np.mean(ion_energies)) if len(ion_energies) else None,
        "energy_std_ev": float(np.std(ion_energies)) if len(ion_energies) else None,
        "fast_neutral_mean_energy_ev": float(np.mean(neutral_energies)) if neutral_energies else None,
        "diagnostics": {"launched_particles": count, "arrived_particles": int(np.count_nonzero(arrived)),
            "escaped_entrance_particles": int(np.count_nonzero(status == 2)),
            "unresolved_particles": int(np.count_nonzero(uncollected)),
            "collected_fraction": float(np.mean(arrived)), "unresolved_fraction": float(np.mean(uncollected)),
            "accounted_particle_fraction": float((np.count_nonzero(arrived) + np.count_nonzero(status == 2) + np.count_nonzero(uncollected)) / count),
            "collision_events": int(np.sum(collisions)), "mean_collisions_per_launch": float(np.mean(collisions)),
            "fast_neutrals_to_electrode": len(neutral_energies), "backward_neutrals": backwards_neutrals,
            "mean_neutral_ballistic_transit_s": float(np.mean(neutral_transit)) if neutral_transit else None,
            "mean_ion_transit_s": float(np.mean(transit[arrived])) if np.any(arrived) else None,
            "mean_rf_periods_in_transit": float(np.mean(transit[arrived]) / period) if np.any(arrived) else None,
            "time_step_s": dt, "max_integration_time_s": max_time, "event_budget_exhausted_particles": event_budget_exhausted,
            "trajectory_energy_balance_residual_ev": energy_residual,
            "trajectory_energy_balance_relative_error": abs(energy_residual) / max(energy_scale, 1e-30),
            "mean_field_work_per_launch_ev": float(np.mean(field_work)),
            "mean_transferred_neutral_energy_per_launch_ev": float(np.mean(transferred_neutral_energy)),
            "mean_new_ion_thermal_energy_per_launch_ev": float(np.mean(thermal_entry_energy)),
            "sheath_collision_optical_depth": optical_depth, "charge_exchange_mean_free_path_m": mean_free_path,
            "charge_exchange_cross_section_m2": cross_section, "maximum_prescribed_drop_v": max_voltage}}


def solve_iedf(waveform: dict[str, Any], settings: dict[str, Any],
               species: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return normalized incident-energy PDFs and Bohm-supplied species fluxes.

    ``voltage_v`` is the positive plasma-to-electrode sheath drop. Species
    density is the density *at the sheath entrance*, not bulk density. A fixed
    slab thickness is supplied or inferred from mean drop and total charge
    density using the cold uniform-ion matrix-sheath relation.
    """
    cycle_t, cycle_v, period, wave_diagnostics = _periodic_waveform(waveform, settings)
    species = species if species is not None else settings.get("species")
    if species is None:
        species = [{"name": f"{settings.get('gas', 'Ar')}+", "mass_amu": settings.get("ion_mass_amu", 39.948),
                    "density_m3": settings.get("ion_density_m3", settings.get("electron_density_m3", 1e16))}]
    if not isinstance(species, list) or not 1 <= len(species) <= 12:
        raise ValueError("IEDF needs 1..12 positive-ion species")
    names = [str(row.get("name", "ion")) for row in species]
    if len(set(names)) != len(names) or any(not name.strip() for name in names):
        raise ValueError("IEDF species names must be nonempty and unique")
    charge_density = sum(_number(row["density_m3"], "density_m3", positive=False) *
                         _integer(row.get("charge_number", 1), "charge_number", 1, 3) for row in species)
    mean_voltage = wave_diagnostics["mean_accelerating_voltage_v"]
    if "sheath_thickness_m" in settings:
        thickness = _number(settings["sheath_thickness_m"], "sheath_thickness_m")
        thickness_source = "explicit_fixed_slab"
    else:
        if charge_density <= 0 or mean_voltage <= 0:
            raise ValueError("Matrix sheath thickness requires positive entrance charge density and mean sheath drop")
        thickness = math.sqrt(2 * EPSILON_0 * mean_voltage / (ELEMENTARY_CHARGE * charge_density))
        thickness_source = "fixed_mean_drop_matrix_sheath"
    gas_temperature = _number(settings.get("gas_temperature_k", 300), "gas_temperature_k")
    neutral_density = _number(settings.get("neutral_density_m3", float(settings.get("pressure_pa", 1.333223684)) /
                                        (BOLTZMANN * gas_temperature)), "neutral_density_m3", positive=False)
    bins = _integer(settings.get("histogram_bins", 80), "histogram_bins", 8, 512)
    seed = _integer(settings.get("seed", 17), "seed", 0, 2**32 - 1)
    rng = np.random.default_rng(seed)
    species_results = [_integrate_species(cycle_t, cycle_v, period, thickness, row, settings,
                                         neutral_density, gas_temperature, rng) for row in species]
    observed = [r["incident_energies_ev"] for r in species_results if len(r["incident_energies_ev"])]
    observed += [r["neutral_energies_ev"] for r in species_results if len(r["neutral_energies_ev"])]
    maximum_energy = max((float(np.max(e)) for e in observed), default=1.0)
    edges = np.linspace(0, max(maximum_energy * (1 + 1e-9), 1e-9), bins + 1)
    widths = np.diff(edges)
    signals: list[dict[str, Any]] = []
    rows = []
    total_flux = sum(r["ion_flux_m2_s"] for r in species_results)
    combined = np.zeros(bins)
    for r in species_results:
        values = r.pop("incident_energies_ev")
        neutrals = r.pop("neutral_energies_ev")
        counts = np.histogram(values, bins=edges)[0]
        probability = counts / len(values) / widths if len(values) else np.zeros(bins)
        combined += probability * r["ion_flux_m2_s"] / total_flux if total_flux > 0 else 0
        signals.append({"name": f"IEDF({r['name']})", "unit": "eV^-1", "values": probability.tolist()})
        if len(neutrals):
            neutral_pdf = np.histogram(neutrals, bins=edges)[0] / len(neutrals) / widths
            signals.append({"name": f"fast-neutral-energy({r['name']})", "unit": "eV^-1", "values": neutral_pdf.tolist()})
        r["diagnostics"]["histogram_integral"] = float(np.sum(probability * widths))
        rows.append([r["name"], r["mass_amu"], r["density_m3"], r["bohm_speed_m_s"],
                     r["ion_flux_m2_s"], r["mean_energy_ev"], r["energy_std_ev"], r["fast_neutral_flux_m2_s"]])
    signals.insert(0, {"name": "IEDF(all ions, flux weighted)", "unit": "eV^-1", "values": combined.tolist()})
    warnings = []
    if wave_diagnostics["negative_sheath_drop_fraction"] > 0:
        warnings.append("Negative supplied sheath drops were clipped to zero: electron-sheath phases are outside this model.")
    if wave_diagnostics["periodic_boundary_relative_mismatch"] > 0.05:
        warnings.append("The supplied final RF cycle has more than 5% endpoint mismatch; periodic continuation is questionable.")
    if any(r["diagnostics"]["unresolved_particles"] for r in species_results):
        warnings.append("Some ions remain unresolved at the integration/event budget: PDFs condition on arrivals and collected flux is incomplete.")
    if any(r["diagnostics"]["escaped_entrance_particles"] for r in species_results):
        warnings.append("Some charge-exchanged ions escaped the entrance; their probability is reported separately from the incident PDF.")
    mean_energy = sum((r["mean_energy_ev"] or 0) * r["ion_flux_m2_s"] for r in species_results) / total_flux if total_flux else None
    variance = sum(((r["energy_std_ev"] or 0)**2 + ((r["mean_energy_ev"] or 0) - (mean_energy or 0))**2) *
                   r["ion_flux_m2_s"] for r in species_results) / total_flux if total_flux else None
    area = math.pi * (_number(settings.get("cathode_diameter_m", 0.3), "cathode_diameter_m") / 2)**2
    unresolved = sum(r["diagnostics"]["unresolved_particles"] for r in species_results)
    return {"kind": "iedf", "converged": unresolved == 0 and total_flux > 0,
        "summary": {"mean_ion_energy_ev": mean_energy, "ion_energy_std_ev": math.sqrt(variance) if variance is not None else None,
            "total_ion_flux_m2_s": total_flux,
            "ion_current_a": ELEMENTARY_CHARGE * area * sum(r["charge_number"] * r["ion_flux_m2_s"] for r in species_results),
            "total_fast_neutral_flux_m2_s": sum(r["fast_neutral_flux_m2_s"] for r in species_results),
            "sheath_thickness_m": thickness, "unresolved_particles": unresolved},
        "axis": {"name": "incident_energy", "unit": "eV", "values": ((edges[:-1] + edges[1:]) / 2).tolist()},
        "signals": signals,
        "tables": [{"name": "Ion species incident flux and energies", "columns": ["species", "mass_amu", "entrance_density_m3",
            "bohm_speed_m_s", "ion_flux_m2_s", "mean_energy_ev", "energy_std_ev", "fast_neutral_flux_m2_s"], "rows": rows}],
        "logs": warnings, "netlist": "", "solver": {"name": "midpoint-field event-resolved test-particle integration", "seed": seed},
        "model_metadata": {"name": "Prescribed RF sheath test-particle IEDF", "version": MODEL_VERSION,
            "status": "reduced_model_unvalidated", "field": "E(t)=max(Vs(t),0)/fixed_sheath_thickness",
            "thickness_source": thickness_source, "entrance": "species-density Bohm flux; stratified uniform RF launch phase",
            "charge_exchange": "user constant cross section, exponential path events; equal-mass resonant velocity replacement at neutral Tg",
            "assumptions": ["Fixed planar slab, spatially uniform electric field, prescribed periodic voltage.",
                "Independent positive ions; no space-charge feedback, ion-ion interactions, elastic scattering, angular distribution, or surface chemistry.",
                "Entrance density is supplied, not inferred from an electronegative Bohm closure.",
                "Charge exchange is optional and its constant user cross section is not a verified gas database.",
                "Fast neutrals travel ballistically after creation without subsequent collisions.",
                "Histogram PDFs are normalized over collected ions; flux separately includes collected/launch fraction."],
            "literature_basis": {"title": "Principles of Plasma Discharges and Materials Processing, 2nd edition", "authors": "Lieberman and Lichtenberg",
                "year": 2005, "url": "https://doi.org/10.1002/0471724254", "use": "Bohm entry and collisionless sheath energy principles; no claim of experimental validation"}},
        "diagnostics": {"waveform": wave_diagnostics, "species": {r["name"]: r["diagnostics"] for r in species_results},
            "species_results": species_results, "histogram_bin_edges_ev": edges.tolist(), "histogram_normalization": "probability density, conditional on electrode arrival",
            "model_domain_valid": not warnings, "domain_warnings": warnings, "neutral_density_m3": neutral_density, "gas_temperature_k": gas_temperature}}

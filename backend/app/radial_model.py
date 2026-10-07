"""Annular linear CCP equivalent circuit and explicit applicability estimates.

Density is a prescribed global value. The radial voltage and absorbed-power
distribution follows nodal KCL; this module does not solve radial chemistry or
Maxwell's equations.
"""
from __future__ import annotations

import cmath
import math
from typing import Any

import numpy as np

from .plasma_models import ELEMENTARY_CHARGE, ELECTRON_MASS, EPSILON_0


MU_0 = 1.25663706212e-6
LIGHT_SPEED = 1 / math.sqrt(MU_0 * EPSILON_0)
MODEL_VERSION = "annular-linear-ccp-circuit-0.1"


def _positive(value: Any, name: str, zero: bool = False) -> float:
    result = float(value)
    if not math.isfinite(result) or (result < 0 if zero else result <= 0):
        raise ValueError(f"{name} must be {'nonnegative' if zero else 'positive'} and finite")
    return result


def _parameters(settings: dict[str, Any], operating_point: dict[str, Any] | None) -> dict[str, Any]:
    values = dict(settings)
    if operating_point:
        values.update(operating_point)
    result = {
        "frequency_hz": _positive(values.get("frequency_hz", 40e6), "frequency_hz"),
        "rf_peak_voltage": _positive(values.get("rf_peak_voltage", 250), "rf_peak_voltage", True),
        "radius_m": _positive(values.get("cathode_diameter_m", .3), "cathode_diameter_m") / 2,
        "gap_m": _positive(values.get("gap_m", .05), "gap_m"),
        "electron_density_m3": _positive(values.get("electron_density_m3", 1e16), "electron_density_m3"),
        "electron_temperature_ev": _positive(values.get("electron_temperature_ev", 3), "electron_temperature_ev"),
        "momentum_collision_frequency_hz": _positive(values.get("momentum_collision_frequency_hz", 1e7), "momentum_collision_frequency_hz", True),
        "ion_density_m3": _positive(values.get("ion_density_m3", values.get("electron_density_m3", 1e16)), "ion_density_m3"),
        "mean_cathode_sheath_voltage_v": _positive(values.get("mean_cathode_sheath_voltage_v", 100), "mean_cathode_sheath_voltage_v"),
        "mean_anode_sheath_voltage_v": _positive(values.get("mean_anode_sheath_voltage_v", 30), "mean_anode_sheath_voltage_v"),
        "return_area_ratio": _positive(values.get("return_area_ratio", values.get("area_ratio", 1)), "return_area_ratio"),
        "electrode_sheet_resistance_ohm": _positive(values.get("electrode_sheet_resistance_ohm", .02), "electrode_sheet_resistance_ohm", True),
        "electrode_sheet_inductance_h": _positive(values.get("electrode_sheet_inductance_h", 2e-9), "electrode_sheet_inductance_h", True),
        "radial_feed": values.get("radial_feed", "center"),
    }
    cells = _positive(values.get("radial_cells", 32), "radial_cells")
    if not cells.is_integer() or isinstance(values.get("radial_cells"), bool) or not 4 <= cells <= 256:
        raise ValueError("radial_cells must be an integer in 4..256")
    result["radial_cells"] = int(cells)
    if result["radial_feed"] not in {"center", "edge"}:
        raise ValueError("radial_feed must be center or edge")
    result["radial_feed_radius_m"] = _positive(values.get("radial_feed_radius_m", min(.01, result["radius_m"] * .1)), "radial_feed_radius_m")
    result["radial_feed_width_m"] = _positive(values.get("radial_feed_width_m", min(.005, result["radius_m"] * .05)), "radial_feed_width_m")
    if result["radial_feed_radius_m"] >= result["radius_m"] or result["radial_feed_width_m"] >= result["radius_m"]:
        raise ValueError("Radial feed radius/width must be smaller than electrode radius")
    ni = result["ion_density_m3"]
    result["cathode_sheath_thickness_m"] = _positive(values.get("cathode_sheath_thickness_m",
        math.sqrt(2 * EPSILON_0 * result["mean_cathode_sheath_voltage_v"] / (ELEMENTARY_CHARGE * ni))), "cathode_sheath_thickness_m")
    result["anode_sheath_thickness_m"] = _positive(values.get("anode_sheath_thickness_m",
        math.sqrt(2 * EPSILON_0 * result["mean_anode_sheath_voltage_v"] / (ELEMENTARY_CHARGE * ni))), "anode_sheath_thickness_m")
    default_bulk = result["gap_m"] - result["cathode_sheath_thickness_m"] - result["anode_sheath_thickness_m"]
    result["bulk_length_m"] = _positive(values.get("bulk_length_m", default_bulk), "bulk_length_m (gap minus mean sheaths)")
    return result


def _impedances(p: dict[str, Any]) -> tuple[complex, complex, float, float, float, float]:
    omega = 2 * math.pi * p["frequency_hz"]
    inductance_area = ELECTRON_MASS * p["bulk_length_m"] / (ELEMENTARY_CHARGE**2 * p["electron_density_m3"])
    resistance_area = inductance_area * p["momentum_collision_frequency_hz"]
    cathode_capacitance_area = EPSILON_0 / p["cathode_sheath_thickness_m"]
    anode_capacitance_area = EPSILON_0 * p["return_area_ratio"] / p["anode_sheath_thickness_m"]
    axial_impedance_area = resistance_area + 1j * omega * inductance_area + 1 / (1j * omega * cathode_capacitance_area) + 1 / (1j * omega * anode_capacitance_area)
    sheet_impedance = p["electrode_sheet_resistance_ohm"] + 1j * omega * p["electrode_sheet_inductance_h"]
    if abs(axial_impedance_area) < 1e-30:
        raise ValueError("Undamped axial series resonance has zero impedance; specify physical momentum damping")
    return axial_impedance_area, sheet_impedance, resistance_area, inductance_area, cathode_capacitance_area, anode_capacitance_area


def electromagnetics_validity(settings: dict[str, Any], operating_point: dict[str, Any] | None = None) -> dict[str, Any]:
    """Homogeneous Drude skin/wavelength and circuit radial-wavelength estimates.

    None denotes an infinite/unbounded length, never a fabricated finite value.
    Warning thresholds are disclosed engineering screening choices, not fitted
    validation boundaries.
    """
    p = _parameters(settings, operating_point)
    zaxial, zsheet, _, _, _, _ = _impedances(p)
    omega = 2 * math.pi * p["frequency_hz"]
    nu = p["momentum_collision_frequency_hz"]
    ne = p["electron_density_m3"]
    wp = math.sqrt(ne * ELEMENTARY_CHARGE**2 / (EPSILON_0 * ELECTRON_MASS))
    debye = math.sqrt(EPSILON_0 * p["electron_temperature_ev"] / (ELEMENTARY_CHARGE * ne))
    sigma = ne * ELEMENTARY_CHARGE**2 / (ELECTRON_MASS * complex(nu, omega))
    # exp(j*w*t), field exp(-j*k*z): negative imaginary k attenuates.
    kbulk = cmath.sqrt((omega / LIGHT_SPEED)**2 - 1j * MU_0 * omega * sigma)
    if kbulk.real < 0:
        kbulk = -kbulk
    if abs(kbulk.real) < 1e-30 and kbulk.imag > 0:
        kbulk = -kbulk
    skin = 1 / abs(kbulk.imag) if abs(kbulk.imag) > 1e-30 else None
    bulk_wavelength = 2 * math.pi / abs(kbulk.real) if abs(kbulk.real) > 1e-30 else None
    # Continuum limit of sheet links plus local shunt branch:
    # laplacian(V)=Zsheet*Yarea*V, hence kradial^2=-Zsheet/Zaxial_area.
    kradial = cmath.sqrt(-zsheet / zaxial)
    if kradial.real < 0:
        kradial = -kradial
    if abs(kradial.real) < 1e-30 and kradial.imag > 0:
        kradial = -kradial
    radial_wavelength = 2 * math.pi / abs(kradial.real) if abs(kradial.real) > 1e-30 else None
    radial_attenuation = 1 / abs(kradial.imag) if abs(kradial.imag) > 1e-30 else None
    ratios = {
        "debye_to_bulk_length": debye / p["bulk_length_m"],
        "bulk_length_to_skin_depth": p["bulk_length_m"] / skin if skin is not None else 0.0,
        "vacuum_phase_over_radius_rad": omega * p["radius_m"] / LIGHT_SPEED,
        "effective_radial_phase_over_radius_rad": abs(kradial.real) * p["radius_m"],
        "radial_cell_complex_phase": abs(kradial) * (p["radius_m"] - (p["radial_feed_radius_m"] if p["radial_feed"] == "center" else p["radial_feed_width_m"])) / (p["radial_cells"] - 1),
        "mean_sheath_fraction_of_gap": (p["cathode_sheath_thickness_m"] + p["anode_sheath_thickness_m"]) / p["gap_m"],
    }
    checks = {"bulk_quasineutral_screen": ratios["debye_to_bulk_length"] <= .1,
        "axial_uniform_current_screen": ratios["bulk_length_to_skin_depth"] <= .3,
        "electroquasistatic_screen": ratios["vacuum_phase_over_radius_rad"] <= .3,
        "radial_mesh_screen": ratios["radial_cell_complex_phase"] <= .2,
        "nonoverlapping_mean_sheaths": ratios["mean_sheath_fraction_of_gap"] < 1}
    warning_text = {
        "bulk_quasineutral_screen": "Debye length exceeds 10% of bulk length; quasineutral bulk impedance is questionable.",
        "axial_uniform_current_screen": "Bulk length exceeds 0.3 Drude skin depths; axial uniform-current branches omit significant electromagnetic penetration structure.",
        "electroquasistatic_screen": "Vacuum RF phase over electrode radius exceeds 0.3 rad; retardation and radiation omitted by the circuit can matter.",
        "radial_mesh_screen": "Complex radial phase per annulus exceeds 0.2; refine radial_cells and check convergence.",
        "nonoverlapping_mean_sheaths": "Mean sheath thicknesses fill the electrode gap; distinct bulk and sheath regions are outside the model domain.",
    }
    warnings = [warning_text[name] for name, passed in checks.items() if not passed]
    return {"debye_length_m": debye, "electron_plasma_frequency_rad_s": wp,
        "electron_momentum_collision_frequency_s": nu, "drude_skin_depth_m": skin,
        "homogeneous_bulk_wavelength_m": bulk_wavelength, "effective_radial_wavelength_m": radial_wavelength,
        "effective_radial_attenuation_length_m": radial_attenuation,
        "bulk_wave_number_real_m_inv": kbulk.real, "bulk_wave_number_imag_m_inv": kbulk.imag,
        "effective_radial_wave_number_real_m_inv": kradial.real, "effective_radial_wave_number_imag_m_inv": kradial.imag,
        "ratios": ratios, "checks": checks, "model_domain_valid": all(checks.values()), "domain_warnings": warnings,
        "threshold_basis": "Explicit engineering screening choices (0.1 Debye ratio, 0.3 skin/vacuum phase, 0.2 cell phase), not empirically validated cutoffs.",
        "assumptions": ["Debye length uses supplied global ne and Maxwellian Te.",
            "Skin depth and bulk wavelength use a homogeneous unmagnetized collisional Drude plasma with exp(j omega t).",
            "Effective radial wavelength is from the distributed sheet/axial circuit, including user sheet impedance; it is not a full Maxwell eigenmode.",
            "All density and collision coefficients are prescribed; none is fitted to force a requested distribution."]}


def _weighted_variation(values: np.ndarray, weights: np.ndarray) -> tuple[float, float]:
    mean = float(np.average(values, weights=weights))
    if abs(mean) < 1e-30:
        return 0.0, 0.0
    return float(math.sqrt(np.average((values - mean)**2, weights=weights)) / abs(mean)), float((np.max(values) - np.min(values)) / abs(mean))


def solve_radial(settings: dict[str, Any], operating_point: dict[str, Any] | None = None) -> dict[str, Any]:
    """Solve the RF peak phasors of a fed annular electrode/axial CCP network."""
    p = _parameters(settings, operating_point)
    count = p["radial_cells"]
    # Keep the physical feed footprint fixed while refining the radial mesh.
    # Otherwise a shrinking center feed tends to a logarithmic point-source
    # singularity and a misleading refinement comparison.
    if p["radial_feed"] == "center":
        edges = np.concatenate(([0.0], np.linspace(p["radial_feed_radius_m"], p["radius_m"], count)))
    else:
        edges = np.concatenate((np.linspace(0, p["radius_m"] - p["radial_feed_width_m"], count), [p["radius_m"]]))
    areas = math.pi * np.diff(edges**2)
    # Area centroids keep the center annulus finite and avoid a log(r=0) feed.
    radii = 2 / 3 * np.diff(edges**3) / np.diff(edges**2)
    zarea, zsheet, rarea, larea, ccarea, caarea = _impedances(p)
    omega = 2 * math.pi * p["frequency_hz"]
    axial_z = zarea / areas
    ylocal = 1 / axial_z
    feed = 0 if p["radial_feed"] == "center" else count - 1
    voltage = np.full(count, complex(p["rf_peak_voltage"], 0))
    matrix = np.diag(ylocal)
    ideal_sheet = abs(zsheet) == 0
    link_geometry = np.log(radii[1:] / radii[:-1])
    # Feed metal is equipotential across its disk/band, so a link starts at its
    # physical boundary rather than accumulating impedance inside the feed.
    if p["radial_feed"] == "center":
        link_geometry[0] = math.log(radii[1] / p["radial_feed_radius_m"])
    else:
        link_geometry[-1] = math.log((p["radius_m"] - p["radial_feed_width_m"]) / radii[-2])
    link_z = zsheet * link_geometry / (2 * math.pi)
    if not ideal_sheet:
        link_y = 1 / link_z
        for i, y in enumerate(link_y):
            matrix[i, i] += y
            matrix[i+1, i+1] += y
            matrix[i, i+1] -= y
            matrix[i+1, i] -= y
        unknown = np.asarray([i for i in range(count) if i != feed])
        voltage[unknown] = np.linalg.solve(matrix[np.ix_(unknown, unknown)], -matrix[unknown, feed] * voltage[feed])
        node_current = matrix @ voltage
        feed_current = node_current[feed]
        kcl_error = float(np.max(np.abs(node_current[unknown])) / max(abs(feed_current), 1e-30))
        link_current = (voltage[:-1] - voltage[1:]) / link_z
        electrode_power = float(.5 * np.sum(np.abs(link_current)**2 * link_z.real))
        # Incoming feed current equals all local shunt currents because internal
        # radial currents cancel pairwise in KCL.
        total_current_residual = abs(feed_current - np.sum(voltage * ylocal)) / max(abs(feed_current), 1e-30)
    else:
        feed_current = np.sum(voltage * ylocal)
        kcl_error = 0.0
        total_current_residual = 0.0
        electrode_power = 0.0
    local_current = voltage * ylocal
    bulk_z = (rarea + 1j * omega * larea) / areas
    cathode_z = 1 / (1j * omega * ccarea * areas)
    anode_z = 1 / (1j * omega * caarea * areas)
    cathode_voltage = local_current * cathode_z
    anode_voltage = local_current * anode_z
    electron_power = .5 * np.abs(local_current)**2 * rarea / areas
    power_density = electron_power / areas
    total_electron_power = float(np.sum(electron_power))
    input_power = float(.5 * (voltage[feed] * np.conj(feed_current)).real)
    power_residual = input_power - total_electron_power - electrode_power
    power_relative_error = abs(power_residual) / max(abs(input_power), total_electron_power + electrode_power, 1e-30)
    voltage_variation, voltage_range = _weighted_variation(np.abs(voltage), areas)
    power_variation, power_range = _weighted_variation(power_density, areas)
    validity = electromagnetics_validity(settings, operating_point)
    cathode_linearization = float(np.max(np.abs(cathode_voltage)) / p["mean_cathode_sheath_voltage_v"])
    anode_linearization = float(np.max(np.abs(anode_voltage)) / p["mean_anode_sheath_voltage_v"])
    linearization_valid = max(cathode_linearization, anode_linearization) <= .3
    validity["ratios"].update({"max_rf_to_mean_cathode_sheath_voltage": cathode_linearization,
                             "max_rf_to_mean_anode_sheath_voltage": anode_linearization})
    validity["checks"]["small_signal_sheath_screen"] = linearization_valid
    if not linearization_valid:
        validity["domain_warnings"].append("RF sheath-voltage amplitude exceeds 30% of its mean drop; linearized capacitances cannot reproduce nonlinear sheath collapse or harmonics.")
    validity["model_domain_valid"] = all(validity["checks"].values())
    rows = [[float(radii[i]), float(areas[i]), p["electron_density_m3"], float(abs(voltage[i])),
             float(np.angle(voltage[i], deg=True)), float(abs(local_current[i])),
             float(abs(cathode_voltage[i])), float(abs(anode_voltage[i])),
             float(electron_power[i]), float(power_density[i])] for i in range(count)]
    condition = float(np.linalg.cond(matrix[np.ix_([i for i in range(count) if i != feed], [i for i in range(count) if i != feed])])) if not ideal_sheet else 1.0
    return {"kind": "radial", "converged": kcl_error < 1e-8 and power_relative_error < 1e-8,
        "summary": {"electron_absorbed_power_w": total_electron_power, "electrode_loss_power_w": electrode_power,
            "rf_input_power_w": input_power, "rf_feed_current_peak_a": float(abs(feed_current)),
            "electrode_voltage_nonuniformity": voltage_variation, "electrode_voltage_range_over_mean": voltage_range,
            "absorbed_power_nonuniformity": power_variation, "absorbed_power_range_over_mean": power_range,
            "electron_density_m3": p["electron_density_m3"], "electron_temperature_ev": p["electron_temperature_ev"],
            "debye_length_m": validity["debye_length_m"], "drude_skin_depth_m": validity["drude_skin_depth_m"],
            "effective_radial_wavelength_m": validity["effective_radial_wavelength_m"]},
        "axis": {"name": "radius", "unit": "m", "values": radii.tolist()},
        "signals": [{"name": "V(electrode) amplitude", "unit": "V", "values": np.abs(voltage).tolist()},
            {"name": "V(electrode) phase", "unit": "deg", "values": np.angle(voltage, deg=True).tolist()},
            {"name": "V(cathode sheath) amplitude", "unit": "V", "values": np.abs(cathode_voltage).tolist()},
            {"name": "V(anode sheath) amplitude", "unit": "V", "values": np.abs(anode_voltage).tolist()},
            {"name": "V(bulk) amplitude", "unit": "V", "values": np.abs(local_current * bulk_z).tolist()},
            {"name": "electron absorbed power density", "unit": "W/m^2", "values": power_density.tolist()},
            {"name": "prescribed global electron density", "unit": "m^-3", "values": [p["electron_density_m3"]] * count}],
        "tables": [{"name": "Annular RF circuit solution", "columns": ["radius_m", "annular_area_m2", "prescribed_density_m3",
            "electrode_voltage_peak_v", "electrode_voltage_phase_deg", "axial_current_peak_a", "cathode_sheath_voltage_peak_v",
            "anode_sheath_voltage_peak_v", "electron_power_w", "electron_power_density_w_m2"], "rows": rows}],
        "logs": validity["domain_warnings"], "netlist": "", "solver": {"name": "complex nodal KCL", "phasor_convention": "peak amplitude, exp(j omega t)"},
        "model_metadata": {"name": "Annular linear CCP equivalent circuit", "version": MODEL_VERSION, "status": "reduced_model_unvalidated",
            "density_model": "user-prescribed uniform global density; no radial particle or chemistry balance solved",
            "feed": p["radial_feed"], "radial_cells": count, "feed_annulus": feed,
            "parameters": p,
            "equations": {"bulk_per_area": "Zbulk*A=me*bulk_length*(nu+j*omega)/(ne*e^2)",
                "sheath_capacitance": "C=dQ/dVs=epsilon0*A/s_mean, s_mean=sqrt(2*epsilon0*Vs_mean/(e*ni))",
                "radial_link": "Zlink=(Rsheet+j*omega*Lsheet)*ln(r_outer_centroid/r_inner_centroid)/(2*pi)",
                "KCL": "Vi/Zaxial_i + sum_neighbors((Vi-Vj)/Zlink_ij)=0 away from ideal feed",
                "power": "Pbulk_i=0.5*abs(Ipeak_i)^2*Rbulk_i"},
            "assumptions": ["Axisymmetric planar annular finite-volume equivalent circuit with ideal grounded return electrode.",
                "Global ne, Te, collision frequency and sheath entrance density are supplied, not solved radially.",
                "Local axial series Drude R/L and two lossless sheath capacitances linearized at positive mean sheath drops.",
                "Radial series impedance describes the user-supplied powered electrode sheet; plasma radial transport is not included.",
                "The ideal center disk or edge band has a fixed supplied physical footprint during mesh refinement; no point-source singularity is modeled.",
                "Return area ratio scales each branch's return-sheath capacitance; lateral spreading in the return is omitted.",
                "Radiation, full electromagnetic surface eigenmodes, skin profiles, nonlinear harmonics and local chemistry are outside this reduced model."],
            "literature_basis": {"title": "Principles of Plasma Discharges and Materials Processing, 2nd edition", "authors": "Lieberman and Lichtenberg",
                "year": 2005, "url": "https://doi.org/10.1002/0471724254", "use": "Drude bulk and differential matrix-sheath elements; nodal circuit is an explicit reduced approximation"}},
        "diagnostics": {"nodal_kcl_relative_residual": kcl_error, "feed_minus_total_axial_current_relative_residual": float(total_current_residual),
            "input_minus_bulk_and_electrode_power_w": power_residual, "power_balance_relative_error": power_relative_error,
            "nodal_matrix_condition_number": condition, "annular_edges_m": edges.tolist(),
            "validity": validity, "model_domain_valid": validity["model_domain_valid"], "domain_warnings": validity["domain_warnings"]}}

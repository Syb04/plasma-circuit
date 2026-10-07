"""Source-26 (Gudmundsson et al., 2000) approximate ion-wall transport.

This is the earlier Eq.(17)--(19) edge-factor approximation, not the spatial
electronegative core/edge closure of the 2001 paper's Eq.(8)--(16).  Constants
and validity inequalities are retained without clipping or calibration.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .plasma_models import ATOMIC_MASS, BOLTZMANN, ELEMENTARY_CHARGE

TRANSPORT_VERSION = "gudmundsson-2000-eq17-19-0.1"
TRANSPORT_SOURCE_URL = "https://doi.org/10.1088/0022-3727/33/11/311"
TRANSPORT_PDF_URL = "https://cden.ucsd.edu/internal/Publications/Archive/SFR/Plasma/PlanarIndOxyDisch.pdf"
DEFAULT_ION_MOMENTUM_CROSS_SECTIONS_M2 = {"O": 7.5e-19, "O2": 7.5e-19}


def gudmundsson_2000_transport(*, temperature_ev: float, ion_temperature_k: float,
                              radius_m: float, length_m: float,
                              neutral_densities_m3: Mapping[str, float],
                              electron_density_m3: float, negative_ion_density_m3: float,
                              ion_momentum_cross_sections_m2: Mapping[str, float],
                              atomic_negative_ion_density_m3: float | None = None) -> dict[str, Any]:
    """Pressure/composition and electronegativity-dependent Eq.(17)--(19).

    Alpha is supplied as *all* modeled negative ions / electrons, an explicit
    extension of the original source's dominant O-minus approximation. The
    common mean free path uses ground O and O2 only. Each positive ion retains
    its own classical Bohm speed/mass. Returned regime tests are necessary
    source inequalities, not proof of physical applicability.
    """
    def positive(value: float, name: str) -> float:
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
        return value

    te = positive(temperature_ev, "temperature_ev")
    ti = positive(ion_temperature_k, "ion_temperature_k")
    radius = positive(radius_m, "radius_m")
    length = positive(length_m, "length_m")
    ne = positive(electron_density_m3, "electron_density_m3")
    negative = float(negative_ion_density_m3)
    if not math.isfinite(negative) or negative < 0:
        raise ValueError("negative_ion_density_m3 must be nonnegative and finite")
    alpha_ominus = None
    if atomic_negative_ion_density_m3 is not None:
        atomic_negative = float(atomic_negative_ion_density_m3)
        if not math.isfinite(atomic_negative) or not 0 <= atomic_negative <= negative:
            raise ValueError("atomic_negative_ion_density_m3 must be between zero and total negative density")
        alpha_ominus = atomic_negative / ne
    if set(ion_momentum_cross_sections_m2) != {"O", "O2"}:
        raise ValueError("ion_momentum_cross_sections_m2 requires exactly O and O2")
    cross_sections = {name: positive(value, f"cross_section_{name}")
                      for name, value in ion_momentum_cross_sections_m2.items()}
    densities = {}
    for name in ("O", "O2"):
        value = float(neutral_densities_m3.get(name, 0))
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"neutral density {name} must be nonnegative and finite")
        densities[name] = value
    inverse_path = math.fsum(densities[name] * cross_sections[name] for name in ("O", "O2"))
    if inverse_path <= 0:
        raise ValueError("A positive ground-neutral collision density is required")
    path = 1 / inverse_path
    alpha = negative / ne
    gamma = ELEMENTARY_CHARGE * te / (BOLTZMANN * ti)
    correction = (1 + 3 * alpha / gamma) / (1 + alpha)
    hl = .86 * correction / math.sqrt(3 + length / (2 * path))
    hr = .8 * correction / math.sqrt(4 + radius / path)
    area = 2 * math.pi * radius * (radius * hl + length * hr)
    volume = math.pi * radius**2 * length
    speeds = {name: math.sqrt(ELEMENTARY_CHARGE * te / (mass * ATOMIC_MASS))
              for name, mass in (("O+", 15.999), ("O2+", 31.998))}
    upper_limit = min(radius, length)
    lower_limit = max(radius, length) / gamma
    regime = {"collisional_upper_bound": path <= upper_limit,
              "ambipolar_lower_bound": path >= lower_limit}
    warnings = []
    if not regime["collisional_upper_bound"]:
        warnings.append("Mean free path does not satisfy lambda_i <= both R and L; Eq.(17)--(18) regime is not established.")
    if not regime["ambipolar_lower_bound"]:
        warnings.append("Mean free path does not satisfy lambda_i >= both R/gamma and L/gamma; Eq.(17)--(18) regime is not established.")
    if hl > 1 or hr > 1:
        warnings.append("An unmodified source edge factor exceeds one; the approximate transport domain is not established.")
    domain_valid = all(regime.values()) and hl <= 1 and hr <= 1
    return {"mode": "gudmundsson_2000", "closure_version": TRANSPORT_VERSION,
            "axial_edge_factor": hl, "radial_edge_factor": hr,
            "ion_mean_free_path_m": path, "inverse_mean_free_path_m": inverse_path,
            "alpha": alpha, "gamma_temperature_ratio": gamma,
            "alpha_ominus": alpha_ominus, "negative_ion_density_m3": negative,
            "electronegative_correction": correction, "ion_temperature_k": ti,
            "ground_neutral_densities_m3": densities, "ion_momentum_cross_sections_m2": cross_sections,
            "effective_area_m2": area, "bohm_speeds_m_s": speeds,
            "wall_rates_s": {"k50": area / volume * speeds["O+"],
                             "k51": area / volume * speeds["O2+"]},
            "domain_valid": domain_valid, "regime_checks": regime,
            "domain_valid_definition": "necessary source ordering conditions only; not sufficient evidence of physical applicability or validation",
            "regime_limits_m": {"upper_inclusive": upper_limit, "lower_inclusive": lower_limit},
            "regime_ratios": {"R_over_lambda": radius / path, "L_over_lambda": length / path,
                              "gamma_lambda_over_R": gamma * path / radius,
                              "gamma_lambda_over_L": gamma * path / length},
            "domain_warnings": warnings,
            "normalization": "source-26 approximate edge-to-bulk positive-ion factors multiplied by bulk positive-ion densities",
            "negative_ion_definition": "sum of O-, O2-, O3- / ne; explicit extension of source's O- approximation"}


def transport_metadata() -> dict[str, Any]:
    return {"closure_version": TRANSPORT_VERSION, "source": TRANSPORT_SOURCE_URL,
            "full_text_url": TRANSPORT_PDF_URL, "location": "Eq.(17)--(19), p.1329",
            "source_formulas": {
                "hL": ".86*(1+3*alpha/gamma)/(1+alpha)/sqrt(3+L/(2*lambda_i))",
                "hR": ".8*(1+3*alpha/gamma)/(1+alpha)/sqrt(4+R/lambda_i)",
                "inverse_mean_free_path": "nO*sigmaO+nO2*sigmaO2",
                "gamma": "e*Te/(kB*Ti)",
                "regime_necessary_conditions": "lambda_i<=R,L and lambda_i>=R/gamma,L/gamma"},
            "assumptions_and_extensions": [
                "The 2000 approximate closure is retained separately from the 2001 spatial core/edge model.",
                "Ti defaults to the supplied gas temperature; it is an assumption, not a solved ion temperature.",
                "Default atomic momentum cross section is 7.5e-19 m2; molecular cross section is assumed equal, following the 2001 source transport candidate.",
                "A common ground O/O2 momentum mean free path is applied to both positive ions; masses enter their individual classical Bohm speeds.",
                "Alpha uses all three modeled negative-ion species, extending the earlier dominant O-minus treatment.",
                "Metastable and ozone momentum targets are omitted from the common mean free path.",
                "The stated inequalities are necessary regime tests; passing them is not literature validation."],
            "source_author_limitations": "The source's conclusion (p.1331) identifies the edge-factor approximation and its generalization to intermediate transport regimes as unresolved.",
            "unimplemented_2001_closure": {
                "reason": "Eq.(8)--(16) require electropositive edge width and core/edge-to-volume density normalization that are not closed by current global inputs.",
                "no_invented_edge_width": True, "no_parameter_calibration": True}}

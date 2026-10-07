"""Independent, provenance-bearing electrode and wall loss parameters.

No surface coefficients are inferred from a material name. In particular,
stainless-steel/Fe oxygen study candidates are not silicon coefficients.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .plasma_models import ATOMIC_MASS, BOLTZMANN

SURFACE_VERSION = "independent-area-weighted-surfaces-0.1"
ROLES = ("cathode", "anode", "wall")
METASTABLES = ("O2(a1Delta)", "O2(b1Sigma)")
_MASS_AMU = {"O": 15.999, "O_1D": 15.999, "O2(a1Delta)": 31.998, "O2(b1Sigma)": 31.998}


def _number(value: Any, name: str, *, positive: bool = False, probability: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0) or (probability and result > 1):
        raise ValueError(f"{name} must be {'in [0,1]' if probability else 'positive' if positive else 'nonnegative'} and finite")
    return result


def _provenance(value: Any) -> Any:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping) and any(isinstance(value.get(k), str) and value[k].strip() for k in ("title", "url", "doi", "citation")):
        return dict(value)
    raise ValueError("Surface provenance must be a nonempty user assumption or source citation")


def _bounds(value: Any, nominal: float, name: str, *, probability: bool = True) -> list[float]:
    if value is None:
        return [nominal, nominal]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} range needs [lower, upper]")
    lower, upper = (_number(v, name, positive=not probability, probability=probability) for v in value)
    if not lower <= nominal <= upper:
        raise ValueError(f"{name} nominal value must lie inside its range")
    return [lower, upper]


def resolve_surface_model(config: Mapping[str, Any] | None, *, areas_m2: Mapping[str, float],
                          plasma_volume_m3: float, neutral_temperature_k: float) -> dict[str, Any]:
    """Compute reaction probabilities, loss coefficients, and sensitivities.

    The reaction-limited, well-mixed closure is k_s = vbar/(4 V) sum A_j
    gamma_s,j. ``effective_boundary_parameters`` also averages gamma/(2-gamma)
    for compatibility with a diffusion-plus-surface resistance closure. No
    diffusion coefficient, surface-temperature dependence, or Si rate is invented.
    """
    if set(areas_m2) != set(ROLES):
        raise ValueError("Surface areas require exactly cathode, anode, and wall")
    areas = {role: _number(areas_m2[role], f"{role} area_m2") for role in ROLES}
    total = math.fsum(areas.values())
    if total <= 0:
        raise ValueError("At least one surface area must be positive")
    volume = _number(plasma_volume_m3, "plasma_volume_m3", positive=True)
    temperature = _number(neutral_temperature_k, "neutral_temperature_k", positive=True)
    if not config:
        return {"version": SURFACE_VERSION, "configured": False, "areas_m2": areas,
                "effective_parameters": None, "effective_boundary_parameters": None,
                "surfaces": {}, "limitations": ["No surface data supplied. Caller retains its explicitly documented existing closure.",
                                                  "No silicon recombination, quenching, or secondary-emission coefficient is supplied by this module."]}
    if set(config) != set(ROLES):
        raise ValueError("surface_parameters requires separate cathode, anode, and wall entries")
    surfaces: dict[str, Any] = {}
    for role in ROLES:
        entry = config[role]
        if not isinstance(entry, Mapping):
            raise ValueError(f"{role} surface parameters must be an object")
        for label in ("material", "state"):
            if not isinstance(entry.get(label), str) or not entry[label].strip():
                raise ValueError(f"{role} requires a material and surface state description")
        ts = _number(entry.get("temperature_k"), f"{role} temperature_k", positive=True)
        go = _number(entry.get("gamma_o"), f"{role} gamma_o", probability=True)
        secondary = _number(entry.get("secondary_electron_yield"), f"{role} secondary_electron_yield", probability=True)
        metastable = entry.get("gamma_metastable")
        if isinstance(metastable, Mapping):
            if set(metastable) != set(METASTABLES):
                raise ValueError(f"{role} gamma_metastable requires exactly {', '.join(METASTABLES)}")
            gm = {species: _number(metastable[species], f"{role} gamma_metastable {species}", probability=True) for species in METASTABLES}
        else:
            gm = {species: _number(metastable, f"{role} gamma_metastable", probability=True) for species in METASTABLES}
        provenance_input = entry.get("provenance", {})
        range_input = entry.get("ranges", {})
        if not isinstance(provenance_input, Mapping) or not isinstance(range_input, Mapping):
            raise ValueError("Surface provenance and ranges must be objects")
        provenance = {key: _provenance(provenance_input.get(key, "Explicit user input; no material-derived coefficient"))
                      for key in ("temperature_k", "gamma_o", "gamma_metastable", "secondary_electron_yield")}
        metastable_ranges = range_input.get("gamma_metastable")
        gm_ranges = {species: _bounds(metastable_ranges.get(species) if isinstance(metastable_ranges, Mapping) else metastable_ranges,
                                      gm[species], f"{role} gamma_metastable {species}") for species in METASTABLES}
        surfaces[role] = {"material": entry["material"].strip(), "state": entry["state"].strip(),
                          "temperature_k": ts, "area_m2": areas[role], "area_fraction": areas[role] / total,
                          "gamma_o": go, "gamma_metastable": gm, "secondary_electron_yield": secondary,
                          "provenance": provenance, "ranges": {
                              "temperature_k": _bounds(range_input.get("temperature_k"), ts, f"{role} temperature_k", probability=False),
                              "gamma_o": _bounds(range_input.get("gamma_o"), go, f"{role} gamma_o"),
                              "gamma_metastable": gm_ranges,
                              "secondary_electron_yield": _bounds(range_input.get("secondary_electron_yield"), secondary, f"{role} secondary_electron_yield")}}
    def weighted(values: Mapping[str, float]) -> float:
        return math.fsum(areas[role] * values[role] for role in ROLES) / total
    go_values = {role: surfaces[role]["gamma_o"] for role in ROLES}
    sec_values = {role: surfaces[role]["secondary_electron_yield"] for role in ROLES}
    gm_values = {species: {role: surfaces[role]["gamma_metastable"][species] for role in ROLES} for species in METASTABLES}
    def boundary_gamma(values: Mapping[str, float]) -> float:
        b = weighted({role: value / (2 - value) for role, value in values.items()})
        return 2 * b / (1 + b)
    effective = {"gamma_o": weighted(go_values), "gamma_metastable": {s: weighted(v) for s, v in gm_values.items()},
                 "secondary_electron_yield": weighted(sec_values)}
    boundary = {"gamma_o": boundary_gamma(go_values), "gamma_metastable": {s: boundary_gamma(v) for s, v in gm_values.items()}}
    coefficients, loss_ranges, contributions, sensitivities = {}, {}, {}, {}
    for species, mass in _MASS_AMU.items():
        speed = math.sqrt(8 * BOLTZMANN * temperature / (math.pi * mass * ATOMIC_MASS))
        values = go_values if species in {"O", "O_1D"} else gm_values[species]
        derivative = {role: speed * areas[role] / (4 * volume) for role in ROLES}
        per_surface = {role: derivative[role] * values[role] for role in ROLES}
        contributions[species], sensitivities[species] = per_surface, derivative
        coefficients[species] = math.fsum(per_surface.values())
        ranges = {role: surfaces[role]["ranges"]["gamma_o"] if species in {"O", "O_1D"}
                  else surfaces[role]["ranges"]["gamma_metastable"][species] for role in ROLES}
        loss_ranges[species] = [math.fsum(derivative[role] * ranges[role][edge] for role in ROLES) for edge in (0, 1)]
    return {"version": SURFACE_VERSION, "configured": True, "areas_m2": areas, "total_area_m2": total,
            "plasma_volume_m3": volume, "neutral_temperature_k": temperature, "surfaces": surfaces,
            "effective_parameters": effective, "effective_boundary_parameters": boundary,
            "neutral_wall_loss_coefficients_s": coefficients, "neutral_wall_loss_ranges_s": loss_ranges,
            "per_surface_neutral_wall_loss_coefficients_s": contributions,
            "sensitivities": {"d_effective_gamma_d_surface_gamma": {role: areas[role] / total for role in ROLES},
                              "d_loss_coefficient_s_d_surface_gamma": sensitivities},
            "formula": "k_wall,s = sqrt(8*kB*Tneutral/(pi*ms))/(4*V) * sum(Aj*gamma_s,j)",
            "boundary_formula": "b=sum(Aj*gamma_j/(2-gamma_j))/sum(Aj); effective_gamma=2*b/(1+b)",
            "limitations": ["Well-mixed isotropic neutral reaction-limited wall loss; no diffusion bottleneck in the reported loss coefficients.",
                            "Surface temperatures and states identify supplied coefficients; no temperature-dependent surface kinetic law is assumed.",
                            "Ranges are user-supplied independent bounds, not statistical confidence intervals.",
                            "Secondary yields are applied per electrode by the RF sheath closure; an area average alone is not an emission-current model.",
                            "No silicon coefficient or stainless-steel/Fe-to-silicon transfer is built in."]}

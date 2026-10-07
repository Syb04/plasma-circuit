"""Sourced electron cross-section/EEDF integration without extrapolated data.

Energy tables are in eV, cross sections in m2, and an EEDF is a normalized
energy probability density in 1/eV (not an EEPF). Returned rates are SI m3/s.
No built-in material or gas cross sections are provided by this module.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
from scipy.special import gammaincc

from .plasma_models import ELEMENTARY_CHARGE, ELECTRON_MASS

TRANSPORT_VERSION = "sourced-cross-section-eedf-0.1"
_GAUSS_X, _GAUSS_W = np.polynomial.legendre.leggauss(24)


def _positive(value: Any, name: str, zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(number) or number < 0 or (number == 0 and not zero):
        raise ValueError(f"{name} must be {'nonnegative' if zero else 'positive'} and finite")
    return number


def _table(energy: Any, values: Any, name: str) -> tuple[np.ndarray, np.ndarray]:
    x, y = np.asarray(energy, dtype=float), np.asarray(values, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or len(x) != len(y) or not 2 <= len(x) <= 20000:
        raise ValueError(f"{name} needs equal one-dimensional tables of 2..20000 points")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or np.any(x < 0) or np.any(y < 0):
        raise ValueError(f"{name} energy and values must be finite and nonnegative")
    if np.any(np.diff(x) <= 0):
        raise ValueError(f"{name} energy_ev must be strictly increasing")
    return x, y


def _source(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, str) and value.strip():
        return {"title": value.strip()}
    if isinstance(value, Mapping) and any(isinstance(value.get(key), str) and value[key].strip() for key in ("title", "url", "doi", "citation")):
        return dict(value)
    raise ValueError(f"{name} requires a nonempty data source/citation")


def _quadrature(grid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    left, right = grid[:-1, None], grid[1:, None]
    return ((right + left) / 2 + (right - left) / 2 * _GAUSS_X,
            (right - left) / 2 * _GAUSS_W)


def _energy_distribution(config: Mapping[str, Any], te: float) -> dict[str, Any]:
    mode = config.get("mode", "maxwellian")
    if mode == "maxwellian":
        # This is the normalized energy PDF, not f(E)/sqrt(E).
        return {"mode": mode, "temperature_ev": te, "mean_energy_ev": 1.5 * te,
                "normalization": 1.0, "source": {"title": "Maxwell-Boltzmann isotropic energy distribution"},
                "formula_per_ev": "2/sqrt(pi)*sqrt(E)/Te**1.5*exp(-E/Te)",
                "support_ev": [0.0, None]}
    if mode != "tabulated":
        raise ValueError("eedf.mode must be maxwellian or tabulated")
    x, f = _table(config.get("energy_ev"), config.get("probability_per_ev"), "tabulated EEDF")
    source = _source(config.get("source"), "Tabulated EEDF")
    norm = float(np.trapz(f, x))
    tolerance = _positive(config.get("normalization_tolerance", 1e-3), "normalization_tolerance")
    if tolerance > .01:
        raise ValueError("EEDF normalization_tolerance must not exceed 0.01")
    if norm <= 0 or abs(norm - 1) > tolerance:
        raise ValueError(f"Tabulated energy PDF must integrate to one; supplied integral is {norm:.9g}")
    f = f / norm
    qx, qw = _quadrature(x)
    mean = float(np.sum(qw * qx * np.interp(qx, x, f)))
    return {"mode": mode, "source": source, "normalization": 1.0,
            "supplied_normalization": norm, "renormalization_factor": 1 / norm,
            "mean_energy_ev": mean, "support_ev": [float(x[0]), float(x[-1])],
            "energy_ev": x.tolist(), "probability_per_ev": f.tolist(),
            "outside_support": "zero by explicit finite-support tabulated-EEDF definition"}


def integrate_cross_section(cross_section: Mapping[str, Any], *, electron_temperature_ev: float,
                            eedf: Mapping[str, Any] | None = None,
                            maxwell_tail_probability_tolerance: float = 1e-6) -> dict[str, Any]:
    """Compute <sigma v> from a sourced table and a normalized energy PDF.

    Cross sections are piecewise linear. Maxwellian tails are *not* extended:
    omitted probability is bounded, reported, and the integral is over supplied
    data only. The tolerance bounds probability, not the unknown rate tail.
    A stated threshold permits exactly-zero cross section below threshold.
    """
    te = _positive(electron_temperature_ev, "electron_temperature_ev")
    distribution = _energy_distribution(eedf or {}, te)
    x, sigma = _table(cross_section.get("energy_ev"), cross_section.get("sigma_m2"), "Cross section")
    source = _source(cross_section.get("source"), "Cross section")
    threshold = cross_section.get("threshold_ev")
    if threshold is not None:
        threshold = _positive(threshold, "threshold_ev", zero=True)
        if threshold > x[-1] or x[0] > threshold:
            raise ValueError("Cross-section data must cover the threshold; no gap above threshold is allowed")
        if np.any(sigma[x < threshold] != 0) or (np.any(x == threshold) and np.any(sigma[x == threshold] != 0) and threshold > 0):
            raise ValueError("Cross sections below a stated threshold must be zero")
    tolerance = _positive(maxwell_tail_probability_tolerance, "maxwell_tail_probability_tolerance")
    if tolerance > .01:
        raise ValueError("Maxwellian tail probability tolerance must not exceed 0.01")
    if distribution["mode"] == "maxwellian":
        if x[0] > 0 and (threshold is None or x[0] > threshold):
            raise ValueError("Maxwellian cross sections must start at zero or an explicitly covered threshold")
        lower = max(float(x[0]), threshold or 0)
        upper = float(x[-1])
        omitted = float(gammaincc(1.5, upper / te))
        if omitted > tolerance:
            raise ValueError(f"Cross-section table does not cover the Maxwellian tail: omitted probability {omitted:.6g} > {tolerance:.6g}")
        # Resolve the exponential even if the supplied cross-section table is
        # coarse. These are integration nodes, never new cross-section data.
        thermal_grid = np.linspace(lower, min(upper, lower + 50 * te), 51)
        grid = np.unique(np.r_[lower, x[(x > lower) & (x < upper)], thermal_grid, upper])
        qx, qw = _quadrature(grid)
        density = 2 / math.sqrt(math.pi) * np.sqrt(qx) / te**1.5 * np.exp(-qx / te)
        subthreshold = float(1 - gammaincc(1.5, (threshold or 0) / te))
    else:
        ex, ef = np.asarray(distribution["energy_ev"]), np.asarray(distribution["probability_per_ev"])
        if ex[-1] > x[-1] or (ex[0] < x[0] and (threshold is None or x[0] > threshold)):
            raise ValueError("Cross-section data do not cover the complete tabulated-EEDF support")
        lower, upper = max(float(ex[0]), threshold or 0), float(ex[-1])
        grid = np.unique(np.r_[lower, x[(x > lower) & (x < upper)], ex[(ex > lower) & (ex < upper)], upper])
        if lower >= upper:
            # The whole EEDF lies below the declared reaction threshold.
            return {"rate_coefficient_m3_s": 0.0, "source": source, "eedf": distribution,
                    "coverage": {"omitted_probability": 0.0, "extrapolation": False, "all_below_threshold": True}}
        qx, qw = _quadrature(grid)
        density = np.interp(qx, ex, ef)
        omitted, subthreshold = 0.0, None
    speed = np.sqrt(2 * ELEMENTARY_CHARGE * qx / ELECTRON_MASS)
    rate = float(np.sum(qw * np.interp(qx, x, sigma) * speed * density))
    return {"rate_coefficient_m3_s": rate, "source": source, "eedf": distribution,
            "coverage": {"cross_section_energy_ev": [float(x[0]), float(x[-1])],
                         "integration_energy_ev": [lower, upper], "omitted_probability": omitted,
                         "zero_cross_section_below_threshold_ev": threshold,
                         "subthreshold_probability": subthreshold, "extrapolation": False,
                         "unknown_tail_rate_not_bounded": distribution["mode"] == "maxwellian"},
            "formula": "k = integral sigma(E)[m2] sqrt(2*e*E/me)[m/s] f(E)[1/eV] dE[eV]"}


def resolve_electron_transport(config: Mapping[str, Any] | None, *, electron_temperature_ev: float,
                               neutral_densities_m3: Mapping[str, float],
                               explicit_nu_s: float | Mapping[str, float]) -> dict[str, Any]:
    """Resolve explicit per-electron nu or imported target/process rate data."""
    config = config or {}
    mode = config.get("mode", "explicit_nu")
    densities = {name: _positive(value, f"neutral density {name}", zero=True)
                 for name, value in neutral_densities_m3.items()}
    if mode == "explicit_nu":
        frequencies = ({name: _positive(value, f"explicit nu {name}", zero=True) for name, value in explicit_nu_s.items()}
                       if isinstance(explicit_nu_s, Mapping) else {"total": _positive(explicit_nu_s, "explicit_nu_s", zero=True)})
        return {"mode": mode, "version": TRANSPORT_VERSION,
                "momentum_collision_frequency_s": math.fsum(frequencies.values()),
                "target_collision_frequencies_s": frequencies, "rate_coefficients_m3_s": {},
                "source": config.get("source", {"title": "Explicit user collision-frequency assumption"}),
                "data_derived": False, "neutral_densities_m3": densities}
    if mode != "cross_section_eedf":
        raise ValueError("electron_transport.mode must be explicit_nu or cross_section_eedf")
    tables = config.get("cross_sections")
    if not isinstance(tables, list) or not tables:
        raise ValueError("cross_section_eedf requires nonempty sourced cross_sections")
    rates, frequencies, reports = {}, {}, {}
    momentum_targets: set[str] = set()
    for table in tables:
        if not isinstance(table, Mapping):
            raise ValueError("Each cross section must be an object")
        identifier, target, process = str(table.get("id", "")).strip(), str(table.get("target", "")).strip(), str(table.get("process", "")).strip()
        if not identifier or identifier in rates or not target or not process:
            raise ValueError("Cross sections require unique id, target, and process")
        if target not in densities:
            raise ValueError(f"Cross-section target {target} has no supplied neutral density")
        report = integrate_cross_section(table, electron_temperature_ev=electron_temperature_ev,
                                         eedf=config.get("eedf"), maxwell_tail_probability_tolerance=config.get("maxwell_tail_probability_tolerance", 1e-6))
        rate = report["rate_coefficient_m3_s"]
        rates[identifier], reports[identifier] = rate, dict(report, target=target, process=process)
        if process == "momentum_transfer":
            if target in momentum_targets:
                raise ValueError(f"Only one total momentum-transfer table per target is allowed: {target}")
            momentum_targets.add(target)
            frequencies[target] = densities[target] * rate
    missing = {target for target, density in densities.items() if density > 0} - momentum_targets
    if missing:
        raise ValueError(f"Missing momentum-transfer data for nonzero neutral targets: {', '.join(sorted(missing))}")
    if not momentum_targets:
        raise ValueError("At least one momentum_transfer cross section is required")
    return {"mode": mode, "version": TRANSPORT_VERSION, "data_derived": True,
            "momentum_collision_frequency_s": math.fsum(frequencies.values()),
            "target_collision_frequencies_s": frequencies, "rate_coefficients_m3_s": rates,
            "processes": reports, "eedf": next(iter(reports.values()))["eedf"],
            "neutral_densities_m3": densities,
            "limitations": ["Isotropic velocity distribution and stationary neutrals; no Boltzmann equation solved.",
                            "Rate integrals use supplied data only; a Maxwellian tail probability bound does not bound an unknown cross-section rate tail.",
                            "Inelastic process rates are returned for explicit chemistry mapping; they do not automatically replace an existing reaction network."]}

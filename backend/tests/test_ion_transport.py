"""Analytic DC energy/flux, RF transit filtering, CX accounting and refinement."""
import json
import math

import numpy as np
import pytest

from app.ion_transport import solve_iedf
from app.plasma_models import ATOMIC_MASS, ELEMENTARY_CHARGE


@pytest.fixture
def settings():
    return {"frequency_hz": 1e7, "electron_temperature_ev": 3, "sheath_thickness_m": .001,
            "particles_per_species": 128, "steps_per_rf_period": 64, "max_transit_periods": 5,
            "histogram_bins": 40, "seed": 23, "neutral_density_m3": 1e21}


@pytest.fixture
def species():
    return [{"name": "Ar+", "mass_amu": 39.948, "density_m3": 2e15}]


def dc_wave(settings, drop=100):
    return {"time_s": [0, 1 / settings["frequency_hz"]], "voltage_v": [drop, drop]}


def test_dc_collisionless_energy_transit_and_bohm_flux_are_analytic(settings, species):
    result = solve_iedf(dc_wave(settings), settings, species)
    mass = species[0]["mass_amu"] * ATOMIC_MASS
    speed = math.sqrt(ELEMENTARY_CHARGE * settings["electron_temperature_ev"] / mass)
    final_speed = math.sqrt(speed**2 + 2 * ELEMENTARY_CHARGE * 100 / mass)
    transit = 2 * settings["sheath_thickness_m"] / (speed + final_speed)
    summary = result["summary"]
    diagnostics = result["diagnostics"]["species"]["Ar+"]
    assert result["converged"]
    assert summary["mean_ion_energy_ev"] == pytest.approx(101.5, rel=1e-12)
    assert summary["ion_energy_std_ev"] < 1e-10
    assert summary["total_ion_flux_m2_s"] == pytest.approx(species[0]["density_m3"] * speed, rel=1e-12)
    assert diagnostics["mean_ion_transit_s"] == pytest.approx(transit, rel=1e-12)
    assert diagnostics["trajectory_energy_balance_relative_error"] < 1e-12
    assert diagnostics["histogram_integral"] == pytest.approx(1)
    json.dumps(result, allow_nan=False)


def test_species_fluxes_use_user_density_mass_and_flux_weighted_moments(settings):
    ions = [{"name": "light+", "mass_amu": 20, "density_m3": 2e15},
            {"name": "heavy+", "mass_amu": 80, "density_m3": 4e15}]
    result = solve_iedf(dc_wave(settings), settings, ions)
    rows = result["diagnostics"]["species_results"]
    assert rows[0]["ion_flux_m2_s"] == pytest.approx(rows[1]["ion_flux_m2_s"])
    assert result["summary"]["mean_ion_energy_ev"] == pytest.approx(101.5)
    widths = np.diff(result["diagnostics"]["histogram_bin_edges_ev"])
    for signal in result["signals"]:
        assert np.dot(signal["values"], widths) == pytest.approx(1)


def test_rf_transit_integrates_waveform_instead_of_instantaneous_voltage(settings, species):
    settings.update({"frequency_hz": 5e7, "sheath_thickness_m": .004, "particles_per_species": 256,
                     "steps_per_rf_period": 128, "max_transit_periods": 30})
    time = np.linspace(0, 1 / settings["frequency_hz"], 513)
    volts = 100 + 90 * np.sin(2 * np.pi * settings["frequency_hz"] * time)
    waveform = {"time_s": time.tolist(), "voltage_v": volts.tolist()}
    result = solve_iedf(waveform, settings, species)
    d = result["diagnostics"]["species"]["Ar+"]
    assert result["converged"]
    assert d["mean_rf_periods_in_transit"] > 10
    assert result["summary"]["ion_energy_std_ev"] < np.std(volts) / 4
    assert result["summary"]["mean_ion_energy_ev"] == pytest.approx(101.5, abs=5)
    assert d["trajectory_energy_balance_relative_error"] < 1e-11


def test_rf_time_step_refinement_stabilizes_mean_and_width(settings, species):
    settings.update({"frequency_hz": 2e7, "sheath_thickness_m": .001,
                     "steps_per_transit": 8, "particles_per_species": 256})
    time = np.linspace(0, 1 / settings["frequency_hz"], 1025)
    waveform = {"time_s": time.tolist(), "voltage_v": (100 + 70 * np.sin(2 * np.pi * settings["frequency_hz"] * time)).tolist()}
    results = [solve_iedf(waveform, dict(settings, steps_per_rf_period=n), species) for n in (32, 64, 128)]
    means = np.asarray([r["summary"]["mean_ion_energy_ev"] for r in results])
    widths = np.asarray([r["summary"]["ion_energy_std_ev"] for r in results])
    assert abs(means[2] - means[1]) < abs(means[1] - means[0])
    assert abs(widths[2] - widths[1]) < abs(widths[1] - widths[0])
    assert abs(means[2] - means[1]) < .1


def test_charge_exchange_is_seeded_reduces_ion_energy_and_tracks_neutrals(settings, species):
    settings.update({"particles_per_species": 512, "charge_exchange_cross_section_m2": 3e-18,
                     "gas_temperature_k": 1e-9, "max_transit_periods": 20})
    result = solve_iedf(dc_wave(settings), settings, species)
    repeat = solve_iedf(dc_wave(settings), settings, species)
    assert result == repeat
    assert result["converged"]
    assert result["summary"]["mean_ion_energy_ev"] < 50
    assert result["summary"]["total_fast_neutral_flux_m2_s"] > result["summary"]["total_ion_flux_m2_s"]
    d = result["diagnostics"]["species"]["Ar+"]
    assert d["mean_collisions_per_launch"] == pytest.approx(3, abs=.3)
    assert d["accounted_particle_fraction"] == 1
    assert d["trajectory_energy_balance_relative_error"] < 1e-12
    assert result["summary"]["mean_ion_energy_ev"] + d["mean_transferred_neutral_energy_per_launch_ev"] == pytest.approx(101.5, abs=1e-8)
    assert any(s["name"].startswith("fast-neutral") for s in result["signals"])


def test_unresolved_particles_remain_explicit_without_false_pdf_normalization(settings, species):
    settings["max_transit_periods"] = .01
    result = solve_iedf(dc_wave(settings), settings, species)
    d = result["diagnostics"]["species"]["Ar+"]
    assert not result["converged"]
    assert d["unresolved_particles"] == settings["particles_per_species"]
    assert d["unresolved_fraction"] == 1
    assert d["histogram_integral"] == 0
    assert result["summary"]["mean_ion_energy_ev"] is None
    assert not result["diagnostics"]["model_domain_valid"]
    json.dumps(result, allow_nan=False)


def test_negative_drop_and_nonperiodic_boundary_are_diagnosed(settings, species):
    waveform = {"time_s": [0, 5e-8, 1e-7], "voltage_v": [-10, 100, 30]}
    result = solve_iedf(waveform, settings, species)
    assert not result["diagnostics"]["model_domain_valid"]
    assert result["diagnostics"]["waveform"]["negative_sheath_drop_fraction"] > 0
    assert len(result["diagnostics"]["domain_warnings"]) >= 2


@pytest.mark.parametrize("changes", [{"particles_per_species": 1}, {"steps_per_rf_period": 0},
                                      {"seed": -1}, {"sheath_thickness_m": 0},
                                      {"charge_exchange_cross_section_m2": -1}])
def test_unphysical_or_unbounded_inputs_are_rejected(settings, species, changes):
    with pytest.raises(ValueError):
        solve_iedf(dc_wave(settings), dict(settings, **changes), species)

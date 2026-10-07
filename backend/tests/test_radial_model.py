"""Uniform analytic limit, nodal/power conservation, physical mesh refinement."""
import json
import math

import numpy as np
import pytest

from app.plasma_models import ELEMENTARY_CHARGE, ELECTRON_MASS, EPSILON_0
from app.radial_model import electromagnetics_validity, solve_radial


@pytest.fixture
def settings():
    return {"frequency_hz": 40e6, "rf_peak_voltage": 10, "cathode_diameter_m": .3,
        "gap_m": .05, "electron_density_m3": 1e16, "electron_temperature_ev": 3,
        "momentum_collision_frequency_hz": 1e7, "ion_density_m3": 1e16,
        "mean_cathode_sheath_voltage_v": 100, "mean_anode_sheath_voltage_v": 30,
        "electrode_sheet_resistance_ohm": .5, "electrode_sheet_inductance_h": 2e-8,
        "radial_feed_radius_m": .01, "radial_feed_width_m": .005, "radial_cells": 24}


def test_ideal_sheet_uniform_limit_matches_series_rl_and_sheaths(settings):
    settings.update({"electrode_sheet_resistance_ohm": 0, "electrode_sheet_inductance_h": 0})
    result = solve_radial(settings)
    p = result["model_metadata"]["parameters"]
    area = math.pi * p["radius_m"]**2
    omega = 2 * math.pi * p["frequency_hz"]
    inductance = ELECTRON_MASS * p["bulk_length_m"] / (p["electron_density_m3"] * ELEMENTARY_CHARGE**2 * area)
    resistance = p["momentum_collision_frequency_hz"] * inductance
    cc = EPSILON_0 * area / p["cathode_sheath_thickness_m"]
    ca = EPSILON_0 * area / p["anode_sheath_thickness_m"]
    impedance = resistance + 1j * omega * inductance + 1 / (1j * omega * cc) + 1 / (1j * omega * ca)
    current = p["rf_peak_voltage"] / abs(impedance)
    assert result["converged"]
    assert result["summary"]["rf_feed_current_peak_a"] == pytest.approx(current, rel=1e-12)
    assert result["summary"]["electron_absorbed_power_w"] == pytest.approx(.5 * current**2 * resistance, rel=1e-12)
    assert result["summary"]["electrode_voltage_nonuniformity"] == 0
    assert result["summary"]["absorbed_power_nonuniformity"] < 1e-14
    assert result["summary"]["effective_radial_wavelength_m"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("feed", ["center", "edge"])
def test_annular_kcl_and_input_power_conserve_with_sheet_loss(settings, feed):
    result = solve_radial(dict(settings, radial_feed=feed))
    diagnostics = result["diagnostics"]
    assert result["converged"]
    assert diagnostics["nodal_kcl_relative_residual"] < 1e-9
    assert diagnostics["feed_minus_total_axial_current_relative_residual"] < 1e-9
    assert diagnostics["power_balance_relative_error"] < 1e-9
    summary = result["summary"]
    assert summary["electrode_loss_power_w"] > 0
    assert summary["rf_input_power_w"] == pytest.approx(summary["electron_absorbed_power_w"] + summary["electrode_loss_power_w"], rel=1e-9)
    assert summary["absorbed_power_nonuniformity"] > .01
    voltages = np.asarray(result["signals"][0]["values"])
    assert voltages[0 if feed == "center" else -1] == settings["rf_peak_voltage"]
    assert "prescribed" in result["model_metadata"]["density_model"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("feed", ["center", "edge"])
def test_radial_refinement_has_fixed_feed_footprint_and_stabilizes_power(settings, feed):
    results = [solve_radial(dict(settings, radial_feed=feed, radial_cells=n)) for n in (16, 32, 64)]
    powers = np.asarray([r["summary"]["electron_absorbed_power_w"] for r in results])
    assert abs(powers[2] - powers[1]) < abs(powers[1] - powers[0])
    assert abs(powers[2] - powers[1]) / powers[2] < .002
    for result in results:
        edges = result["diagnostics"]["annular_edges_m"]
        assert edges[1] == settings["radial_feed_radius_m"] if feed == "center" else edges[-2] == pytest.approx(.15 - settings["radial_feed_width_m"])


def test_drude_debye_estimates_and_domain_screening_are_explicit(settings):
    result = electromagnetics_validity(settings)
    debye = math.sqrt(EPSILON_0 * settings["electron_temperature_ev"] / (ELEMENTARY_CHARGE * settings["electron_density_m3"]))
    assert result["debye_length_m"] == pytest.approx(debye, rel=1e-12)
    assert result["drude_skin_depth_m"] > 0
    assert result["effective_radial_wavelength_m"] > 0
    assert result["effective_radial_wave_number_imag_m_inv"] < 0
    assert not result["checks"]["axial_uniform_current_screen"]
    assert "screening" in result["threshold_basis"]
    assert result["domain_warnings"]
    assert result["bulk_wave_number_imag_m_inv"] < 0
    collisionless = electromagnetics_validity(dict(settings, momentum_collision_frequency_hz=0))
    assert collisionless["bulk_wave_number_imag_m_inv"] < 0
    assert collisionless["homogeneous_bulk_wavelength_m"] is None


def test_large_rf_sheath_amplitude_is_flagged_despite_numeric_conservation(settings):
    result = solve_radial(dict(settings, rf_peak_voltage=250))
    assert result["converged"]
    assert not result["diagnostics"]["validity"]["checks"]["small_signal_sheath_screen"]
    assert not result["diagnostics"]["model_domain_valid"]
    assert any("linearized" in text for text in result["logs"])


def test_operating_point_overrides_are_used_in_differential_sheaths(settings):
    base = solve_radial(settings)
    changed = solve_radial(settings, {"electron_density_m3": 2e16, "ion_density_m3": 4e16,
                                     "mean_cathode_sheath_voltage_v": 200})
    p0 = base["model_metadata"]["parameters"]
    p1 = changed["model_metadata"]["parameters"]
    assert p1["cathode_sheath_thickness_m"] / p0["cathode_sheath_thickness_m"] == pytest.approx(math.sqrt(.5))
    assert changed["summary"]["electron_density_m3"] == 2e16


@pytest.mark.parametrize("changes", [{"radial_feed": "both"}, {"radial_cells": 3}, {"radial_cells": 4.5},
    {"electrode_sheet_resistance_ohm": -1}, {"mean_cathode_sheath_voltage_v": 0},
    {"gap_m": 1e-6}, {"radial_feed_radius_m": .2}])
def test_unphysical_radial_parameters_are_rejected(settings, changes):
    with pytest.raises(ValueError):
        solve_radial(dict(settings, **changes))

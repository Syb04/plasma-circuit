import math

import numpy as np
import pytest
from scipy.integrate import quad

from app.electron_heating import moving_wall_power_flux, resolve_electron_heating
from app.plasma_models import ELEMENTARY_CHARGE, ELECTRON_MASS


def arguments(amplitude=1e-4):
    time = np.linspace(0, 1e-6, 2049)
    return {"time_s": time, "bulk_current_a": np.sin(2 * math.pi * time / 1e-6), "bulk_resistance_ohm": 10,
            "sheath_voltage_v": {"cathode": np.ones_like(time) * 100}, "sheath_areas_m2": {"cathode": .01},
            "electron_density_m3": 1e16, "electron_temperature_ev": 3, "sheath_ion_density_m3": 1e16,
            "sheath_position_m": {"cathode": 1e-3 + amplitude * np.sin(2 * math.pi * time / 1e-6)}}


def test_drude_matches_mean_ri_squared_and_ignores_reversible_sheath_work():
    result = resolve_electron_heating({"mode": "bulk_drude"}, **arguments(), electrode_absorbed_power_w=7,
                                     conductive_sheath_power_w=2)
    assert result["electron_heating_w"] == pytest.approx(5)
    assert result["electron_sheath_moving_wall_w"] == 0
    assert result["budget_valid"]
    assert not result["budget"]["sheath_capacitor_vi_included"]


def test_moving_wall_energy_integral_independently_matches_maxwellian_reflections():
    density, te, u = 1e16, 3, 2e4
    thermal = math.sqrt(ELEMENTARY_CHARGE * te / ELECTRON_MASS)
    # v=thermal*z; integrate collision flux times lab-frame reflection energy.
    moment = quad(lambda z: (u - thermal * z)**2 * math.exp(-z**2 / 2) / math.sqrt(2 * math.pi),
                  -12, u / thermal, epsabs=1e-3)[0]
    direct = 2 * ELECTRON_MASS * density * u * moment
    report = moving_wall_power_flux(np.array([u]), electron_density_m3=density, electron_temperature_ev=te)
    assert report["total_reflection_work_w_m2"][0] == pytest.approx(direct, rel=1e-12)
    assert report["irreversible_w_m2"][0] == pytest.approx(direct - density * ELEMENTARY_CHARGE * te * u, rel=1e-12)


def test_stationary_and_nonreflecting_sheath_have_zero_heating():
    stationary = resolve_electron_heating({"mode": "moving_wall_maxwellian"}, **arguments(0))
    assert stationary["electron_sheath_moving_wall_w"] == pytest.approx(0, abs=1e-20)
    absent = resolve_electron_heating({"mode": "moving_wall_maxwellian", "reflection_probability": 0}, **arguments())
    assert absent["electron_sheath_moving_wall_w"] == 0


def test_periodic_motion_separates_pressure_work_and_slow_wall_quadratic_limit():
    first = resolve_electron_heating({"mode": "moving_wall_maxwellian"}, **arguments())
    second = resolve_electron_heating({"mode": "moving_wall_maxwellian"}, **arguments(2e-4))
    assert first["electron_sheath_moving_wall_w"] > 0
    assert abs(first["mean_reversible_pressure_work_w"]) < 1e-6 * first["electron_sheath_moving_wall_w"]
    assert second["electron_sheath_moving_wall_w"] / first["electron_sheath_moving_wall_w"] == pytest.approx(4, rel=2e-6)
    assert first["domain_valid"]


def test_supplemental_estimate_exposes_unclosed_power_budget_without_rescaling():
    result = resolve_electron_heating({"mode": "moving_wall_maxwellian"}, **arguments(), electrode_absorbed_power_w=7,
                                     conductive_sheath_power_w=2, secondary_electron_acceleration_power_w=.5)
    assert result["electron_heating_w"] == pytest.approx(5.5 + result["electron_sheath_moving_wall_w"])
    assert result["budget"]["remaining_conductive_sheath_channel_w"] == 1.5
    assert result["budget"]["extended_rf_power_residual_w"] == pytest.approx(-result["electron_sheath_moving_wall_w"])
    # Tightening the explicit budget detects the independent unclosed estimate.
    tight = resolve_electron_heating({"mode": "moving_wall_maxwellian", "budget_relative_tolerance": 1e-8},
                                    **arguments(), electrode_absorbed_power_w=7, conductive_sheath_power_w=2)
    assert tight["budget_valid"] is False
    assert not tight["budget"]["rf_backreaction_included"]


def test_actual_series_power_closes_budget_and_is_counted_once():
    estimate = resolve_electron_heating({"mode": "moving_wall_maxwellian"}, **arguments())["electron_sheath_moving_wall_w"]
    result = resolve_electron_heating({"mode": "moving_wall_maxwellian", "effective_circuit_backreaction": True},
                                     **arguments(), electrode_absorbed_power_w=7 + estimate,
                                     conductive_sheath_power_w=2, circuit_sheath_heating_power_w=estimate)
    assert result["electron_heating_w"] == pytest.approx(5 + estimate)
    assert result["budget_valid"]
    assert result["budget"]["rf_backreaction_included"]
    assert result["budget"]["extended_rf_power_residual_w"] == pytest.approx(0, abs=1e-14)


def test_configured_sheath_resistor_and_secondary_transfer_are_each_counted_once():
    result = resolve_electron_heating({"mode": "bulk_drude"}, **arguments(),
        circuit_sheath_resistor_power_w=1, secondary_electron_acceleration_power_w=.5,
        electrode_absorbed_power_w=8, conductive_sheath_power_w=2)
    assert result["electron_heating_w"] == pytest.approx(6.5)
    assert result["electron_sheath_moving_wall_w"] == 0
    assert result["electron_sheath_configured_resistor_w"] == 1
    assert result["budget_valid"]
    assert result["budget"]["extended_rf_power_residual_w"] == pytest.approx(0, abs=1e-14)


def test_nonperiodic_or_attracting_sheath_is_flagged():
    data = arguments()
    data["sheath_position_m"]["cathode"] += np.linspace(0, .001, len(data["time_s"]))
    data["sheath_voltage_v"]["cathode"][0] = -1
    result = resolve_electron_heating({"mode": "moving_wall_maxwellian"}, **data)
    assert not result["domain_valid"]
    assert len(result["domain_warnings"]) >= 2

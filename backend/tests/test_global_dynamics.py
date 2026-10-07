"""Actual integration, conservation, source-domain and thermal-limit checks."""
import json
import math

import numpy as np
import pytest

from app.global_dynamics import GlobalTransientModel, PowerPulse, execute_global_transient
from app.plasma_models import ATOMIC_MASS, BOLTZMANN as KB, ELEMENTARY_CHARGE as E, ELECTRON_MASS


OXYGEN_CASE = {"gas": "O2", "gas_temperature_k": 600, "wall_edge_factor": .2,
    "cathode_diameter_m": .304, "gap_m": .076, "absorbed_power_w": 500,
    "electron_momentum_nu_s": {"O": 0, "O2": 0}, "stop_time_s": .001}


def run(settings):
    return execute_global_transient({}, {"kind": "global_transient", "settings": settings})


def signal(result, name):
    return np.asarray(next(row["values"] for row in result["signals"] if row["name"] == name))


def test_argon_time_evolution_is_solved_and_conserves_coupled_energy_and_atoms():
    result = run({"gas": "Ar", "absorbed_power_w": 500, "stop_time_s": .001})
    assert result["converged"]
    assert result["solver"]["global"] == "scipy.integrate.solve_ivp"
    assert result["diagnostics"]["solver_evaluations"] > 10
    assert result["summary"]["total_absorbed_energy_j"] == pytest.approx(.5, rel=1e-8)
    assert signal(result, "n(e)")[-1] > 5*signal(result, "n(e)")[0]
    assert np.array_equal(signal(result, "n(e)"), signal(result, "n(Ar+)"))
    assert np.ptp(signal(result, "Te")) > .05
    assert signal(result, "Tg")[-1] > signal(result, "Tg")[0]
    for key in ("electron_energy_residual_j", "gas_energy_residual_j", "total_energy_residual_j"):
        assert abs(result["diagnostics"][key]) < 1e-10
    assert result["diagnostics"]["max_atom_source_relative_error"] < 1e-12
    json.dumps(result, allow_nan=False)


def test_oxygen_steady_limit_preserves_all_species_charge_pressure_and_energy():
    result = run(OXYGEN_CASE)
    assert result["converged"]
    assert np.ptp(signal(result, "Tg")) < 1e-8
    for row in result["signals"]:
        if row["name"].startswith("n("):
            values = np.asarray(row["values"])
            assert np.max(abs(values/values[0]-1)) < 3e-5
            assert np.min(values) >= 0
    positive = signal(result, "n(O+)")+signal(result, "n(O2+)")
    negative = signal(result, "n(O-)")+signal(result, "n(O2-)")+signal(result, "n(O3-)")
    assert np.allclose(positive, negative+signal(result, "n(e)"), rtol=1e-12)
    assert result["diagnostics"]["max_atom_source_relative_error"] < 1e-10
    assert abs(result["diagnostics"]["total_energy_residual_j"]) < 1e-10
    assert "not 48 reaction energies" in result["model_metadata"]["limitations"][1]


def test_rectangular_power_pulse_integrates_exact_on_off_energy_and_real_states():
    result = run({"gas": "Ar", "stop_time_s": 1e-5, "absorbed_power_w": 500,
        "pulse_frequency_hz": 1e5, "pulse_duty_cycle": .5, "pulse_off_fraction": .5})
    assert result["converged"]
    assert result["summary"]["total_absorbed_energy_j"] == pytest.approx(500*1e-5*.75, rel=1e-8)
    assert set(signal(result, "P(total_absorbed)")) == {250, 500}
    assert np.ptp(signal(result, "n(e)")) > 1e16
    assert abs(result["diagnostics"]["total_energy_residual_j"]) < 1e-10
    pulse = PowerPulse(1e5, .5, .5)
    assert pulse.factor(1e-5) == 1
    assert pulse.factor(.5e-5) == .5


def test_gas_wall_plus_flow_heat_balance_matches_independent_exponential_limit():
    settings = {"gas": "Ar", "stop_time_s": 1e-4, "initial_gas_temperature_k": 400,
        "background_gas_temperature_k": 300, "feed_temperature_k": 300,
        "gas_heat_capacity_j_k": .01, "gas_wall_conductance_w_k": 100,
        "momentum_collision_frequency_hz": 0, "gas_inelastic_heating_fraction": 0,
        "gas_ion_heating_fraction": 0, "initial_electron_density_m3": 1e17}
    model = GlobalTransientModel(settings)
    result = run(settings)
    assert result["converged"]
    expected = 300+100*math.exp(-(100+model.feed*2.5*KB)*1e-4/.01)
    assert result["summary"]["gas_temperature_k"] == pytest.approx(expected, rel=2e-6)
    assert result["diagnostics"]["gas_energy_residual_j"] == pytest.approx(0, abs=1e-10)


def test_temperature_fit_range_crossing_stops_instead_of_extrapolating():
    result = run({"gas": "Ar", "absorbed_power_w": 1e8, "stop_time_s": 1e-4})
    assert not result["converged"]
    assert result["diagnostics"]["termination"] == "electron_temperature_above_fit_domain"
    assert result["summary"]["electron_temperature_ev"] == pytest.approx(7, abs=1e-7)
    assert result["summary"]["integrated_time_s"] < 1e-4
    assert max(signal(result, "Te")) <= 7+1e-7
    json.dumps(result, allow_nan=False)


def test_rf_refresh_uses_evolving_ne_te_species_and_separate_power_budget():
    calls = []

    def constant_rf_for_budget(parsed):
        calls.append(parsed)
        return {"converged": True, "summary": {"electron_heating_w": 400,
            "ion_acceleration_power_w": 100, "conductive_sheath_power_w": 80, "electrode_absorbed_power_w": 480}}

    result = execute_global_transient({}, {"kind": "global_transient", "settings": {
        "gas": "Ar", "power_mode": "rf_coupled", "stop_time_s": 1e-5,
        "rf_update_interval_s": 2e-6}}, constant_rf_for_budget)
    assert result["converged"]
    assert len(calls) == 5
    assert calls[-1].electron_density_m3 != calls[0].electron_density_m3
    assert calls[-1].neutral_species_densities_m3["Ar"] != calls[0].neutral_species_densities_m3["Ar"]
    assert result["summary"]["total_absorbed_energy_j"] == pytest.approx(480e-5, rel=1e-8)
    assert abs(result["diagnostics"]["total_energy_residual_j"]) < 1e-10
    assert result["model_metadata"]["assumptions"]["RF"].startswith("actual cycle-average")


def test_macro_pulse_is_applied_once_and_carrier_solver_is_unpulsed():
    calls = []
    def carrier(parsed):
        calls.append(parsed)
        return {"converged": True, "summary": {"electron_heating_w": 400, "ion_acceleration_power_w": 100,
            "conductive_sheath_power_w": 80, "electrode_absorbed_power_w": 480}}
    result = execute_global_transient({}, {"settings": {"gas": "Ar", "power_mode": "rf_coupled",
        "stop_time_s": 1e-5, "rf_update_interval_s": 5e-6, "pulse_frequency_hz": 1e5,
        "pulse_duty_cycle": .5, "pulse_off_fraction": .5}}, carrier)
    assert result["converged"]
    assert result["summary"]["total_absorbed_energy_j"] == pytest.approx(480e-5*.75, rel=1e-8)
    assert all(call.pulse_frequency_hz is None for call in calls)


def test_rf_off_secondary_channels_cancel_to_net_floating_ambipolar_loss():
    model = GlobalTransientModel({"gas": "Ar", "power_mode": "rf_coupled",
        "secondary_electron_yield_cathode": .2, "secondary_electron_yield_anode": .1})
    state = model.evaluate(model.initial, 0)
    gamma = model.rf_secondary_yield
    te = state["te"]
    velocity = math.sqrt(E*te/(39.948*ATOMIC_MASS))
    phi = te*math.log(math.sqrt(E*te/(2*math.pi*ELECTRON_MASS))/((1+gamma)*model.edge*velocity))
    assert state["floating_sheath_potential_v"] == pytest.approx(phi)
    assert state["total_input_w"] == 0
    assert state["electron_input_w"] == pytest.approx(gamma*state["ion_acceleration_w"])
    assert state["electron_retarding_sheath_work_w"] == pytest.approx((1+gamma)*state["ion_acceleration_w"])
    assert state["electron_retarding_sheath_work_w"]-state["electron_input_w"] == pytest.approx(state["ion_acceleration_w"])


@pytest.mark.parametrize("settings", [{"gas": "CF4"}, {"pulse_duty_cycle": 2},
    {"gas_heat_capacity_j_k": 0}, {"gas_ion_heating_fraction": 2}, {"output_points": 3.5},
    {"initial_electron_temperature_ev": 8}, {"macro_relative_tolerance": .1}])
def test_invalid_macro_inputs_are_rejected(settings):
    with pytest.raises(ValueError):
        run(settings)

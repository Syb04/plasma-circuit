import math

import pytest

from app.plasma_models import ATOMIC_MASS, BOLTZMANN
from app.surface_models import resolve_surface_model


def surfaces():
    return {role: {"material": material, "state": "explicit conditioned-surface assumption", "temperature_k": 300,
                   "gamma_o": gamma, "gamma_metastable": gamma / 10, "secondary_electron_yield": gamma / 2,
                   "provenance": {"gamma_o": "Synthetic fixture; not Si data"},
                   "ranges": {"gamma_o": [gamma / 2, min(1, gamma * 2)]}}
            for role, material, gamma in (("cathode", "Si", .1), ("anode", "stainless steel", .2), ("wall", "quartz", .3))}


def resolve(config=None, areas=None):
    return resolve_surface_model(config if config is not None else surfaces(), areas_m2=areas or {"cathode": 1, "anode": 2, "wall": 3},
                                 plasma_volume_m3=.5, neutral_temperature_k=600)


def test_independent_surface_area_weighting_and_neutral_loss_units():
    result = resolve()
    gamma = (1 * .1 + 2 * .2 + 3 * .3) / 6
    speed = math.sqrt(8 * BOLTZMANN * 600 / (math.pi * 15.999 * ATOMIC_MASS))
    assert result["effective_parameters"]["gamma_o"] == pytest.approx(gamma)
    assert result["neutral_wall_loss_coefficients_s"]["O"] == pytest.approx(speed * 6 * gamma / (4 * .5))
    assert sum(result["per_surface_neutral_wall_loss_coefficients_s"]["O"].values()) == pytest.approx(result["neutral_wall_loss_coefficients_s"]["O"])
    assert result["surfaces"]["cathode"]["material"] == "Si"
    assert "Synthetic" in result["surfaces"]["cathode"]["provenance"]["gamma_o"]


def test_zero_surface_probability_and_zero_area_remove_loss_contributions():
    config = surfaces()
    for entry in config.values():
        entry.update(gamma_o=0, gamma_metastable=0, secondary_electron_yield=0, ranges={})
    assert all(value == 0 for value in resolve(config)["neutral_wall_loss_coefficients_s"].values())
    result = resolve(areas={"cathode": 1, "anode": 0, "wall": 0})
    assert result["effective_parameters"]["gamma_o"] == .1
    assert result["sensitivities"]["d_loss_coefficient_s_d_surface_gamma"]["O"]["wall"] == 0


def test_area_gamma_derivative_predicts_actual_perturbation_and_uncertainty_bounds():
    baseline = resolve()
    config = surfaces()
    config["wall"]["gamma_o"] += .01
    changed = resolve(config)
    derivative = baseline["sensitivities"]["d_loss_coefficient_s_d_surface_gamma"]["O"]["wall"]
    assert changed["neutral_wall_loss_coefficients_s"]["O"] - baseline["neutral_wall_loss_coefficients_s"]["O"] == pytest.approx(.01 * derivative)
    lower, upper = baseline["neutral_wall_loss_ranges_s"]["O"]
    assert lower < baseline["neutral_wall_loss_coefficients_s"]["O"] < upper
    assert baseline["effective_boundary_parameters"]["gamma_o"] > baseline["effective_parameters"]["gamma_o"]


def test_temperature_state_does_not_invent_a_material_kinetic_law():
    baseline = resolve()
    config = surfaces()
    config["cathode"]["temperature_k"] = 900
    config["cathode"]["state"] = "oxide"
    assert resolve(config)["neutral_wall_loss_coefficients_s"] == baseline["neutral_wall_loss_coefficients_s"]
    empty = resolve_surface_model(None, areas_m2={"cathode": 1, "anode": 1, "wall": 0}, plasma_volume_m3=1, neutral_temperature_k=300)
    assert not empty["configured"]
    assert empty["effective_parameters"] is None


@pytest.mark.parametrize("changes", [{"gamma_o": 1.1}, {"material": ""}, {"temperature_k": 0}, {"ranges": {"gamma_o": [.2, .3]}}, {"provenance": {"gamma_o": ""}}])
def test_no_invalid_surface_or_out_of_bounds_nominal(changes):
    config = surfaces()
    config["cathode"].update(changes)
    with pytest.raises(ValueError):
        resolve(config)

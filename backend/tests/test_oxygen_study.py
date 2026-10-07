"""Independent conservation/energy and failed-solution checks for oxygen study."""
from dataclasses import replace
import json
import math

import numpy as np
import pytest

from app.oxygen import load_oxygen_data, oxygen_excitation_losses, oxygen_rates
from app.oxygen_study import (
    ALL_NAMES, ELECTRON_MASS_KG, HEAVY_NAMES, NEUTRAL_NAMES, StudyInputs,
    _CaseModel, flatten_study_result, sccm_to_particles_s, solve_oxygen_case,
)
from app.plasma_models import BOLTZMANN, ELEMENTARY_CHARGE


@pytest.fixture(scope="module")
def inputs():
    return StudyInputs(absorbed_power_w=500, pressure_pa=10 * .133322368,
                       axial_edge_factor=.2, radial_edge_factor=.2,
                       diffusion_o_m2_s=1.2, diffusion_o2_m2_s=.84,
                       electron_momentum_nu_s={"O": 1e7, "O2": 1e7})


@pytest.fixture(scope="module")
def baseline(inputs):
    return solve_oxygen_case(inputs)


def test_baseline_balances_all_species_pressure_atoms_charge_and_total_energy(inputs, baseline):
    assert baseline["success"]
    assert baseline["source_domain_valid"]
    assert all(baseline["residuals"]["checks"].values())
    assert baseline["residuals"]["max_relative"] < 1e-6
    n = baseline["densities_m3"]
    assert set(n) == set(ALL_NAMES)
    assert all(value > 0 for value in n.values())
    assert 1 < baseline["temperature_ev"] < 4.5
    assert sum(n[name] for name in NEUTRAL_NAMES) == pytest.approx(inputs.pressure_pa / (BOLTZMANN * inputs.gas_temperature_k), rel=1e-6)
    assert n["O+"] + n["O2+"] == pytest.approx(n["e"] + n["O-"] + n["O2-"] + n["O3-"], rel=1e-12)
    atoms = {s["name"]: s["elements"].get("O", 0) for s in load_oxygen_data()["species"]}
    atom_out = baseline["pumping_rate_s"] * baseline["volume_m3"] * sum(atoms[name] * n[name] for name in NEUTRAL_NAMES)
    assert atom_out == pytest.approx(2 * sccm_to_particles_s(inputs.flow_sccm), rel=1e-6)
    assert sum(value for name, value in baseline["loss_w"].items() if name != "total") == pytest.approx(inputs.absorbed_power_w, rel=1e-6)
    # Atom closure also restricts the unknown pump independently of chemistry.
    pump_ref = baseline["feed_molecules_s"] / (baseline["volume_m3"] * baseline["neutral_density_target_m3"])
    assert 2 / 3 * pump_ref <= baseline["pumping_rate_s"] <= 2 * pump_ref


def test_reaction_events_match_published_rates_and_energy_is_counted_once(inputs, baseline):
    data = load_oxygen_data()
    n = baseline["densities_m3"]
    te = baseline["temperature_ev"]
    k = oxygen_rates(te, inputs.gas_temperature_k)
    events = baseline["reaction_event_rates_m3_s"]
    assert len(events) == 48
    assert "k49" not in events
    for reaction in data["reactions"]:
        if not reaction["implemented"]:
            continue
        expected = k[reaction["id"]] * math.prod(n[name]**count for name, count in reaction["reactants"].items())
        assert events[reaction["id"]] == pytest.approx(expected, rel=1e-12)
    conversion = ELEMENTARY_CHARGE * baseline["volume_m3"]
    energy = data["supplementary_ionization_energies_ev"]
    iz = conversion * n["e"] * (n["O2"] * k["k1"] * energy["O2"]["value"] + n["O"] * k["k4"] * energy["O"]["value"])
    ex_coeff = oxygen_excitation_losses(te)
    ex = conversion * n["e"] * (n["O2"] * ex_coeff["O2"] + n["O"] * ex_coeff["O"])
    assert baseline["loss_w"]["ionization"] == pytest.approx(iz, rel=1e-12)
    assert baseline["loss_w"]["excitation"] == pytest.approx(ex, rel=1e-12)
    wall = baseline["electron_wall_loss_m3_s"]
    assert wall == pytest.approx(n["O+"] * baseline["wall_rates_s"]["k50"] + n["O2+"] * baseline["wall_rates_s"]["k51"])
    assert baseline["loss_w"]["electron_wall"] == pytest.approx(conversion * wall * 2 * te)
    assert baseline["loss_w"]["ion_wall"] == pytest.approx(conversion * wall * (baseline["plasma_potential_v"] + te / 2))
    area = 2 * math.pi * inputs.radius_m * (inputs.radius_m + inputs.length_m)
    electron_flux = n["e"] * math.sqrt(ELEMENTARY_CHARGE * te / (2 * math.pi * ELECTRON_MASS_KG)) * math.exp(-baseline["plasma_potential_v"] / te)
    assert area * electron_flux == pytest.approx(baseline["volume_m3"] * wall, rel=1e-12)


def test_explicit_zero_elastic_and_high_frequency_cases_rebalance_without_fitting(inputs):
    zero = solve_oxygen_case(replace(inputs, electron_momentum_nu_s={"O": 0, "O2": 0}))
    high = solve_oxygen_case(replace(inputs, electron_momentum_nu_s={"O": 1e8, "O2": 1e8}), initial_result=zero)
    assert zero["success"] and high["success"]
    assert zero["loss_w"]["elastic"] == 0
    assert high["loss_w"]["elastic"] > 0
    assert zero["electron_density_m3"] > high["electron_density_m3"]
    assert zero["metadata"]["closures"]["zero_elastic"]
    assert high["metadata"]["no_parameter_calibration"]
    assert zero["loss_w"]["total"] == pytest.approx(high["loss_w"]["total"], rel=1e-6)


def test_optimizer_termination_does_not_accept_incomplete_or_source_boundary_solution(inputs):
    unfinished = solve_oxygen_case(inputs, max_nfev=1, multistarts=1)
    assert not unfinished["success"]
    assert unfinished["residuals"]["max_relative"] > 1e-6
    outside_domain = solve_oxygen_case(replace(inputs, pressure_pa=.133322368,
                                               diffusion_o_m2_s=8.8, diffusion_o2_m2_s=6.3), multistarts=2)
    assert not outside_domain["success"]
    assert not outside_domain["source_domain_valid"]
    assert "no_temperature_bound" in outside_domain["failure_reasons"]
    assert outside_domain["per_reaction_range_warnings"][0]["reaction"] == "k20"
    assert outside_domain["temperature_ev"] <= 4.5


def test_minor_species_uses_own_production_loss_scale_and_density_bounds_reject(inputs):
    model = _CaseModel(inputs)
    x = model.seed(0)
    state = model.evaluate(x)
    index = ALL_NAMES.index("O3-")
    state["production"][index] = 1.1 * state["loss"][index]
    diagnostics = model.diagnostics(state, x, 1e-6)
    assert diagnostics["species_relative"]["O3-"] == pytest.approx(1 / 11)
    assert not diagnostics["checks"]["species"]
    x[7] = model.lower[7]
    diagnostics = model.diagnostics(model.evaluate(x), x, 1e-6)
    assert not diagnostics["checks"]["no_density_bound"]


def test_results_are_json_csv_serializable_and_validation_limits_are_disclosed(baseline):
    json.dumps(baseline, allow_nan=False)
    row = flatten_study_result(baseline)
    assert all(value is None or isinstance(value, (str, float, int, bool)) for value in row.values())
    assert row["electron_momentum_nu_O_s"] == 1e7
    assert row["electron_momentum_nu_O2_s"] == 1e7
    metadata = baseline["metadata"]
    assert not metadata["literature_reproduction_success"]
    assert metadata["unresolved_energy_channels"]
    assert metadata["source_values"]["implemented_reaction_count"] == 48
    assert metadata["source_values"]["excitation_term_count"] == 16
    assert metadata["solver_temperature_domain_ev"] == [1, 4.5]
    assert metadata["individual_reaction_domains"]["k20"]["endpoints"] == "exclusive"
    assert "not all 48 reaction energies" in metadata["closures"]["energy"]
    assert len(HEAVY_NAMES) == 10


@pytest.mark.parametrize("changes", [
    {"absorbed_power_w": 0}, {"pressure_pa": -1}, {"flow_sccm": math.nan},
    {"gamma_o": 1.1}, {"gamma_meta": -.1}, {"axial_edge_factor": 0},
    {"diffusion_o_m2_s": 0}, {"electron_momentum_nu_s": {"O": 1}},
    {"electron_momentum_nu_s": {"O": -1, "O2": 0}},
])
def test_explicit_inputs_reject_invalid_or_implicit_transport(inputs, changes):
    with pytest.raises(ValueError):
        replace(inputs, **changes)


def test_flow_conversion_defines_standard_conditions():
    expected = 50e-6 / 60 * 101325 / (BOLTZMANN * 273.15)
    assert sccm_to_particles_s(50) == pytest.approx(expected)

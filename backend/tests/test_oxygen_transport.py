"""Source-26 approximate transport, applicability flags and legacy regression."""
from dataclasses import replace
import json
import math

import pytest

from app.oxygen_study import StudyInputs, flatten_study_result, solve_oxygen_case
from app.oxygen_transport import gudmundsson_2000_transport, transport_metadata
from app.plasma_models import ATOMIC_MASS, BOLTZMANN, ELEMENTARY_CHARGE


@pytest.fixture
def transport_inputs():
    return {"temperature_ev": 3, "ion_temperature_k": 600, "radius_m": .152, "length_m": .076,
            "neutral_densities_m3": {"O": 3e19, "O2": 1e20},
            "electron_density_m3": 1e17, "negative_ion_density_m3": 2e16,
            "atomic_negative_ion_density_m3": 1.8e16,
            "ion_momentum_cross_sections_m2": {"O": 7.5e-19, "O2": 7.5e-19}}


@pytest.fixture
def study_inputs():
    return StudyInputs(absorbed_power_w=500, pressure_pa=10 * .133322368,
                       axial_edge_factor=.2, radial_edge_factor=.2,
                       diffusion_o_m2_s=1.2, diffusion_o2_m2_s=.84,
                       electron_momentum_nu_s={"O": 1e7, "O2": 1e7})


def test_source_equations_and_species_bohm_speeds_independently_recompute(transport_inputs):
    r = gudmundsson_2000_transport(**transport_inputs)
    path = 1 / (3e19 * 7.5e-19 + 1e20 * 7.5e-19)
    gamma = 3 * ELEMENTARY_CHARGE / (BOLTZMANN * 600)
    correction = (1 + 3 * .2 / gamma) / 1.2
    hl = .86 * correction / math.sqrt(3 + .076 / (2 * path))
    hr = .8 * correction / math.sqrt(4 + .152 / path)
    assert r["ion_mean_free_path_m"] == pytest.approx(path, rel=1e-14)
    assert r["axial_edge_factor"] == pytest.approx(hl, rel=1e-14)
    assert r["radial_edge_factor"] == pytest.approx(hr, rel=1e-14)
    assert r["alpha"] == .2
    assert r["alpha_ominus"] == .18
    area_volume = 2 * (.152 * hl + .076 * hr) / (.152 * .076)
    assert r["wall_rates_s"]["k50"] == pytest.approx(area_volume * math.sqrt(ELEMENTARY_CHARGE * 3 / (15.999 * ATOMIC_MASS)))
    assert r["wall_rates_s"]["k51"] == pytest.approx(area_volume * math.sqrt(ELEMENTARY_CHARGE * 3 / (31.998 * ATOMIC_MASS)))
    assert r["wall_rates_s"]["k50"] / r["wall_rates_s"]["k51"] == pytest.approx(math.sqrt(2))
    assert r["domain_valid"]
    assert all(value > 1 for value in r["regime_ratios"].values())


def test_electropositive_limit_pressure_and_electronegativity_dependence(transport_inputs):
    zero = dict(transport_inputs, negative_ion_density_m3=0, atomic_negative_ion_density_m3=0)
    reference = gudmundsson_2000_transport(**zero)
    assert reference["electronegative_correction"] == 1
    path = reference["ion_mean_free_path_m"]
    assert reference["axial_edge_factor"] == pytest.approx(.86 / math.sqrt(3 + .076 / (2 * path)))
    dense = gudmundsson_2000_transport(**dict(zero, neutral_densities_m3={"O": 3e20, "O2": 1e21}))
    electronegative = gudmundsson_2000_transport(**transport_inputs)
    for direction in ("axial_edge_factor", "radial_edge_factor"):
        assert dense[direction] < reference[direction]
        assert electronegative[direction] < reference[direction]
    assert dense["ion_mean_free_path_m"] == pytest.approx(path / 10)


def test_domain_is_necessary_ordering_and_unclipped_edge_factors_are_exposed(transport_inputs):
    dilute = gudmundsson_2000_transport(**dict(transport_inputs, neutral_densities_m3={"O": 3e15, "O2": 1e16}))
    dense = gudmundsson_2000_transport(**dict(transport_inputs, neutral_densities_m3={"O": 3e23, "O2": 1e24}))
    assert not dilute["domain_valid"] and not dilute["regime_checks"]["collisional_upper_bound"]
    assert not dense["domain_valid"] and not dense["regime_checks"]["ambipolar_lower_bound"]
    assert dilute["domain_warnings"] and dense["domain_warnings"]
    hot_ions = gudmundsson_2000_transport(**dict(transport_inputs, ion_temperature_k=1e7,
                                                negative_ion_density_m3=1e20))
    assert hot_ions["axial_edge_factor"] > 1
    assert not hot_ions["domain_valid"]
    assert "necessary" in hot_ions["domain_valid_definition"]
    # Source ordering is inclusive and has no invented extra margin.
    upper_endpoint = gudmundsson_2000_transport(**dict(transport_inputs, radius_m=.125, length_m=.25,
                                                     neutral_densities_m3={"O": 8, "O2": 0},
                                                     ion_momentum_cross_sections_m2={"O": 1, "O2": 1}))
    assert upper_endpoint["ion_mean_free_path_m"] == .125
    assert upper_endpoint["regime_checks"]["collisional_upper_bound"]
    assert upper_endpoint["regime_limits_m"]["upper_inclusive"] == .125


def test_default_explicit_mode_preserves_prior_baseline_and_legacy_input_roundtrip(study_inputs):
    legacy = dict(study_inputs.__dict__)
    for name in ("transport_mode", "ion_temperature_k", "ion_momentum_cross_sections_m2"):
        legacy.pop(name)
    result = solve_oxygen_case(legacy)
    assert result["success"]
    assert result["temperature_ev"] == pytest.approx(3.5385270795113275, rel=1e-10)
    assert result["electron_density_m3"] == pytest.approx(8.572558425313789e16, rel=1e-10)
    assert result["transport"]["mode"] == "explicit_h"
    assert result["transport"]["axial_edge_factor"] == .2
    assert result["transport_domain_valid"] is None
    assert result["transport"]["ion_mean_free_path_m"] is None


def test_derived_transport_updates_particle_sheath_and_energy_consistently(study_inputs):
    inputs = replace(study_inputs, axial_edge_factor=None, radial_edge_factor=None,
                     transport_mode="gudmundsson_2000")
    result = solve_oxygen_case(inputs)
    assert result["success"] and result["transport_domain_valid"]
    assert all(result["residuals"]["checks"].values())
    assert result["transport"]["axial_edge_factor"] != result["transport"]["radial_edge_factor"]
    assert result["transport"]["ion_temperature_k"] == inputs.gas_temperature_k
    n = result["densities_m3"]
    expected = gudmundsson_2000_transport(
        temperature_ev=result["temperature_ev"], ion_temperature_k=inputs.gas_temperature_k,
        radius_m=inputs.radius_m, length_m=inputs.length_m,
        neutral_densities_m3={"O": n["O"], "O2": n["O2"]},
        electron_density_m3=n["e"], negative_ion_density_m3=n["O-"] + n["O2-"] + n["O3-"],
        ion_momentum_cross_sections_m2=inputs.ion_momentum_cross_sections_m2)
    for name in ("k50", "k51"):
        assert result["wall_rates_s"][name] == pytest.approx(expected["wall_rates_s"][name])
    wall_flux = n["O+"] * expected["wall_rates_s"]["k50"] + n["O2+"] * expected["wall_rates_s"]["k51"]
    assert result["electron_wall_loss_m3_s"] == pytest.approx(wall_flux)
    assert result["loss_w"]["total"] == pytest.approx(inputs.absorbed_power_w, rel=1e-6)
    json.dumps(result, allow_nan=False)
    row = flatten_study_result(result)
    assert row["transport_mode"] == "gudmundsson_2000"
    assert row["transport_domain_valid"]
    assert row["transport_ion_mean_free_path_m"] > 0


def test_numeric_balance_can_pass_outside_transport_ordering_but_is_flagged(study_inputs):
    result = solve_oxygen_case(replace(study_inputs, pressure_pa=100 * .133322368,
                                       axial_edge_factor=None, radial_edge_factor=None,
                                       transport_mode="gudmundsson_2000"))
    assert result["success"]
    assert not result["transport_domain_valid"]
    assert result["transport"]["domain_warnings"]
    assert not result["metadata"]["literature_reproduction_success"]
    assert result["metadata"]["transport"]["source_approximation"]["unimplemented_2001_closure"]["no_invented_edge_width"]


@pytest.mark.parametrize("changes", [
    {"transport_mode": "gudmundsson_2001"}, {"axial_edge_factor": None},
    {"transport_mode": "gudmundsson_2000"}, {"ion_temperature_k": 0},
    {"ion_momentum_cross_sections_m2": {"O": 7.5e-19}},
    {"ion_momentum_cross_sections_m2": {"O": 0, "O2": 7.5e-19}},
])
def test_transport_inputs_require_unambiguous_mode_and_physical_parameters(study_inputs, changes):
    with pytest.raises(ValueError):
        replace(study_inputs, **changes)


def test_source_metadata_discloses_approximation_and_temperature_extension():
    metadata = transport_metadata()
    assert "p.1329" in metadata["location"]
    assert "2001" in metadata["unimplemented_2001_closure"]["reason"] or "Eq.(8)" in metadata["unimplemented_2001_closure"]["reason"]
    assert any("Ti defaults" in value for value in metadata["assumptions_and_extensions"])
    assert any("all three" in value for value in metadata["assumptions_and_extensions"])
    assert "p.1331" in metadata["source_author_limitations"]

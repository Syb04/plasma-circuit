"""Conservation, dimensions and source boundaries for the oxygen transcription."""
import math

import pytest

from app.oxygen import (
    load_oxygen_data, neutral_wall_loss_rate, oxygen_excitation_losses,
    oxygen_network_data, oxygen_rates, oxygen_wall_rates,
)
from app.plasma_models import ReactionNetwork


def test_all_eleven_published_species_and_all_rates_are_retained():
    data = load_oxygen_data()
    assert len(data["species"]) == 11
    assert {s["name"] for s in data["species"]} == {
        "e", "O2", "O2_a", "O", "O_1D", "O3", "O+", "O2+", "O-", "O2-", "O3-"}
    assert {r["id"] for r in data["reactions"]} == {f"k{i}" for i in range(1, 50)}
    assert data["electron_distribution"] == "Maxwellian"
    assert data["temperature_range_ev"] == [1, 7]
    assert not data["validation"]["successful_reproduction"]


def test_implemented_reactions_conserve_charge_and_oxygen_and_have_order_units():
    data = load_oxygen_data()
    species = {s["name"]: s for s in data["species"]}
    for row in data["reactions"]:
        assert row["source"] and row["rate_formula"]
        assert row["order"] == sum(row["reactants"].values())
        assert row["rate_units"] == {2: "m^3/s", 3: "m^6/s"}[row["order"]]
        if not row["implemented"]:
            continue
        for attribute, target in (("charge", None), ("elements", "O")):
            def total(side):
                return sum(count * (species[name][attribute] if target is None
                                    else species[name][attribute].get(target, 0))
                           for name, count in row[side].items())
            assert total("reactants") == total("products"), row["id"]


def test_published_inconsistency_is_omitted_and_conservative_edits_are_disclosed():
    data = load_oxygen_data()
    invalid = [r for r in data["reactions"] if not r["implemented"]]
    assert [r["id"] for r in invalid] == ["k49"]
    assert invalid[0]["reactants"] == {"O2+": 1, "O_1D": 1}
    assert data["omitted_reactions"][0]["reaction"] == "k49"
    assert {c["reaction"] for c in data["source_corrections"]} == {"k24", "k26", "k44", "k45"}
    network = oxygen_network_data()
    assert len(network["species"]) == 10
    assert len(network["reactions"]) == 48
    assert network["energy_mode"].startswith("particle_only")


def test_rate_values_and_three_body_density_factor_normalization():
    rates = oxygen_rates(3, 600)
    assert rates["k1"] == pytest.approx(9e-16 * 3**2 * math.exp(-12.6/3))
    assert rates["k26"] == pytest.approx(2.26e-42 * math.sqrt(.5))
    assert rates["k44"] == pytest.approx(6.9e-40 * .5**(-1.25))
    assert rates["k45"] == 3.82e-40
    assert all(math.isfinite(v) and v >= 0 for v in rates.values())
    # Paper k48 uses Tg rather than Te; preserve that published dependency.
    assert oxygen_rates(1, 600)["k48"] == oxygen_rates(7, 600)["k48"]
    assert oxygen_rates(3, 300)["k48"] == pytest.approx(2.11e-13)


def test_network_import_and_particle_sources_conserve_elements_and_charge():
    network = ReactionNetwork(oxygen_network_data(), "O2")
    densities = {name: 1e16 for name in network.species}
    densities.update({"O2": 1e20, "O+": 1e17, "O2+": 1e17})
    terms, loss = network.gas_source_terms(densities, 3)
    assert loss == 0  # This adapter explicitly handles only particle sources.
    for weights in ({s.name: s.elements["O"] for s in network.species.values()},
                    {s.name: s.charge for s in network.species.values()} | {"e": -1}):
        contributions = [weights.get(name, 0)*term for name, term in terms.items()]
        assert abs(math.fsum(contributions)) < 1e-12*math.fsum(map(abs, contributions))


@pytest.mark.parametrize("te", [0, .99, 7.01, math.inf, math.nan])
def test_source_fit_temperature_range_is_enforced(te):
    with pytest.raises(ValueError):
        oxygen_rates(te)
    with pytest.raises(ValueError):
        oxygen_excitation_losses(te)


def test_wall_returns_conserve_oxygen_and_surface_probability_is_not_a_rate():
    data = load_oxygen_data()
    atoms = {s["name"]: s["elements"].get("O", 0) for s in data["species"]}
    for row in data["wall_reactions"]:
        lhs = sum(atoms[n]*c for n,c in row["reactants"].items())
        rhs = sum(atoms[n]*c for n,c in row["products"].items())
        assert lhs == rhs
    assert neutral_wall_loss_rate(1.2, 15.999, 600, .152, .076, 0) == 0
    small = neutral_wall_loss_rate(1.2, 15.999, 600, .152, .076, .01)
    large = neutral_wall_loss_rate(1.2, 15.999, 600, .152, .076, .17)
    assert 0 < small < large
    # The rate varies with geometry, whereas the surface probability does not.
    assert large != pytest.approx(.17)
    assert large != neutral_wall_loss_rate(1.2, 15.999, 600, .3, .2, .17)


def test_wall_transport_is_an_explicit_input_and_negative_ions_are_trapped():
    with pytest.raises(ValueError, match="diffusion"):
        oxygen_wall_rates(3, 600, .152, .076, axial_edge_factor=.2,
                          diffusion_coefficients_m2_s={})
    result = oxygen_wall_rates(3, 600, .152, .076, axial_edge_factor=.2,
                               diffusion_coefficients_m2_s={"O": 1.2, "O2": .84})
    assert set(result) == {"k50", "k51", "k52", "k53", "k54"}
    assert all(v > 0 for v in result.values())
    assert load_oxygen_data()["wall_assumptions"]["negative_ion_wall_loss_s"] == 0


def test_excitation_energy_uses_thresholds_separate_from_fit_activation():
    data = load_oxygen_data()
    assert len(data["electron_excitation"]) == 16
    row = next(r for r in data["electron_excitation"]
               if r["target"] == "O" and r["excited_state"] == "1D")
    assert row["threshold_ev"] == 1.96
    assert row["rate_formula"]["activation_ev"] == 2.286
    assert all(r["threshold_ev"] >= 0 and r["source"] for r in data["electron_excitation"])
    loss = oxygen_excitation_losses(3)
    assert set(loss) == {"O", "O2"}
    assert all(value > 0 for value in loss.values())
    assert data["energy_balance"]["missing_terms"]
    assert data["supplementary_ionization_energies_ev"]["O2"]["value"] == 12.0697
    assert data["supplementary_ionization_energies_ev"]["O2"]["value"] != 12.6


def test_metadata_does_not_claim_silicon_validation_or_zero_reference_uncertainty():
    data = load_oxygen_data()
    assert "SUS" in data["display_name_ja"]
    assert data["published_conditions"]["wall_material"] == "stainless steel"
    assert "not Si" in data["wall_assumptions"]["silicon_applicability"]
    assert all(p["extraction_uncertainty_relative"] is None for p in data["comparison_points"])
    data["species"].clear()
    assert len(load_oxygen_data()["species"]) == 11

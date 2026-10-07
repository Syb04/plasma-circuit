"""Reaction accounting tests use synthetic coefficients, never a gas dataset."""
from __future__ import annotations

import copy
import json
import math

import pytest

from app.plasma_models import ELEMENTARY_CHARGE, ConstantRate, RateTable, ReactionNetwork


TEST_SOURCE = "Synthetic numerical test only; no physical rate applicability"


def _network_data(reactions=None):
    return {
        "gas": "O2", "name": "numerical conservation fixture", "version": "test",
        "source": TEST_SOURCE, "temperature_range_ev": [1, 7],
        "species": [
            {"name": "O2", "charge": 0, "mass_amu": 32, "elements": {"O": 2}, "reservoir": True},
            {"name": "O2+", "charge": 1, "mass_amu": 32, "elements": {"O": 2}},
            {"name": "O-", "charge": -1, "mass_amu": 16, "elements": {"O": 1}},
            {"name": "O", "charge": 0, "mass_amu": 16, "elements": {"O": 1}},
            {"name": "O2_a", "charge": 0, "mass_amu": 32, "elements": {"O": 2}},
        ],
        "reactions": reactions or [_reaction("ionization", {"e": 1, "O2": 1}, {"e": 2, "O2+": 1}, 3, 2, 12)],
    }


def _reaction(name, reactants, products, coefficient, order, loss=0):
    return {"name": name, "reactants": reactants, "products": products,
            "rate": {"type": "constant", "coefficient": coefficient,
                     "units": {1: "s^-1", 2: "m^3/s", 3: "m^6/s"}[order]},
            "electron_energy_loss_ev": loss, "source": TEST_SOURCE}


def _densities():
    # m^-3; deliberately tiny numerical values make stoichiometry transparent.
    return {"O2": 100, "O2+": 10, "O-": 4, "O": 5, "O2_a": 8}


def _assert_gas_conservation(model, terms):
    charges = {name: species.charge for name, species in model.species.items()} | {"e": -1}
    assert sum(charges[name]*source for name, source in terms.items()) == pytest.approx(0, abs=1e-12)
    for element in {e for species in model.species.values() for e in species.elements}:
        assert sum(species.elements.get(element, 0)*terms[name]
                   for name, species in model.species.items()) == pytest.approx(0, abs=1e-12)


@pytest.mark.parametrize("reaction,expected", [
    (_reaction("decay", {"O2_a": 1}, {"O2": 1}, 2, 1),
     {"O2_a": -16, "O2": 16, "e": 0}),
    (_reaction("ionization", {"e": 1, "O2": 1}, {"e": 2, "O2+": 1}, 3, 2),
     {"O2": -1800, "O2+": 1800, "e": 1800}),
    (_reaction("recombination", {"O": 2, "O2": 1}, {"O2": 2}, .02, 3),
     {"O": -100, "O2": 50, "e": 0}),
    (_reaction("three-body attachment", {"e": 1, "O2": 1, "O": 1}, {"O-": 1, "O2": 1}, .02, 3),
     {"O": -60, "O-": 60, "e": -60}),
])
def test_first_second_third_order_sources_and_conservation(reaction, expected):
    model = ReactionNetwork(_network_data([reaction]), "O2")
    terms, energy = model.gas_source_terms(_densities(), 3)
    assert model.reactions[0].order == sum(reaction["reactants"].values())
    assert model.quasineutral_electron_density(_densities()) == 6
    for name, source in terms.items():
        assert source == pytest.approx(expected.get(name, 0))
    _assert_gas_conservation(model, terms)
    assert energy == 0
    dynamic, _ = model.source_terms(_densities(), 3)
    assert set(dynamic) == set(model.dynamic)
    assert "O2" not in dynamic and "e" not in dynamic


def test_signed_electron_energy_is_per_event_and_superelastic_heats():
    excitation = _reaction("excitation", {"e": 1, "O2": 1}, {"e": 1, "O2_a": 1}, .01, 2, 2)
    superelastic = _reaction("superelastic", {"e": 1, "O2_a": 1}, {"e": 1, "O2": 1}, .5, 2, -2)
    model = ReactionNetwork(_network_data([excitation, superelastic]), "O2")
    terms, energy = model.gas_source_terms(_densities(), 3)
    # Events: .01*6*100 = 6 excitations; .5*6*8 = 24 reverse events.
    assert terms["O2_a"] == pytest.approx(-18)
    assert terms["e"] == 0
    assert energy == pytest.approx((6*2 - 24*2)*ELEMENTARY_CHARGE, rel=1e-12, abs=0)
    assert energy < 0
    _assert_gas_conservation(model, terms)


def test_wall_returns_fractional_reservoir_products_and_closes_charge():
    data = _network_data()
    for species in data["species"]:
        if species["name"] == "O":
            species.update(wall_loss_s=2, wall_products={"O2": .5}, wall_source=TEST_SOURCE)
        elif species["name"] == "O2_a":
            species.update(wall_loss_s=3, wall_products={"O2": 1}, wall_source=TEST_SOURCE)
        elif species["name"] == "O2+":
            species.update(wall_products={"O2": 1}, wall_source=TEST_SOURCE)
        elif species["name"] == "O-":
            species.update(wall_products={"O": 1}, wall_source=TEST_SOURCE)
    model = ReactionNetwork(data, "O2")
    # Override the positive-ion Bohm rate and explicitly supplied negative-ion loss.
    walls = model.wall_source_terms(_densities(), {"O2+": 4, "O-": 1})
    assert walls == pytest.approx({"O2": 69, "O2+": -40, "O-": -4,
                                  "O": -6, "O2_a": -24, "e": -36})
    _assert_gas_conservation(model, walls)
    gas, energy = model.gas_source_terms(_densities(), 3)
    combined, combined_energy = model.source_terms(_densities(), 3, {"O2+": 4, "O-": 1})
    assert combined == pytest.approx({name: gas[name] + walls[name] for name in model.dynamic})
    assert combined_energy == energy
    assert model.metadata()["element_conservation_checked"] is True
    json.dumps(model.metadata(), allow_nan=False)


@pytest.mark.parametrize("returned_species,expected", [
    ("O2+", {"O2+": 0, "O2_a": 0, "e": 0}),
    ("O2_a", {"O2+": 16, "O2_a": -16, "e": 16}),
])
def test_wall_electron_closure_uses_net_charge_after_charged_returns(returned_species, expected):
    data = _network_data()
    species = next(row for row in data["species"] if row["name"] == returned_species)
    species.update(wall_products={"O2+": 1}, wall_source=TEST_SOURCE)
    model = ReactionNetwork(data, "O2")
    walls = model.wall_source_terms(_densities(), {returned_species: 2})
    assert walls == pytest.approx({name: expected.get(name, 0) for name in model.species} | {"e": expected["e"]})
    _assert_gas_conservation(model, walls)


def test_legacy_rates_species_and_source_terms_are_compatible():
    data = _network_data()
    data.pop("temperature_range_ev")
    for species in data["species"]:
        species.pop("elements")
    data["reactions"][0]["rate"] = {"temperature_ev": [1, 7], "coefficients_m3_s": [3, 3]}
    model = ReactionNetwork(data, "O2")
    terms, energy = model.source_terms(_densities(), 3, {"O2+": 4})
    assert terms["O2+"] == pytest.approx(1800 - 40)
    assert energy == pytest.approx(1800*12*ELEMENTARY_CHARGE, rel=1e-12, abs=0)
    assert (model.min_temperature, model.max_temperature) == (1, 7)
    assert model.metadata()["element_conservation_checked"] is False
    assert RateTable((1, 2), (1, 100)).evaluate(1.5) == pytest.approx(10)


def test_mixed_tables_constants_and_declared_temperature_ranges_intersect():
    data = _network_data([
        _reaction("decay", {"O2_a": 1}, {"O2": 1}, 2, 1),
        _reaction("ionization", {"e": 1, "O2": 1}, {"e": 2, "O2+": 1}, 3, 2),
    ])
    data["temperature_range_ev"] = [2, 6]
    data["reactions"][1]["rate"] = {"temperature_ev": [1, 5], "coefficients": [1, 100], "units": "m^3/s"}
    model = ReactionNetwork(data, "O2")
    assert (model.min_temperature, model.max_temperature) == (2, 5)
    assert model.reactions[1].rate.evaluate(3) == pytest.approx(10)
    for temperature in (1.9, 5.1, math.nan, math.inf):
        with pytest.raises(ValueError, match="温度"):
            model.source_terms(_densities(), temperature)
    data["temperature_range_ev"] = [6, 7]
    with pytest.raises(ValueError, match="共通"):
        ReactionNetwork(data, "O2")


def test_nonbimolecular_temperature_tables_have_explicit_units():
    first = RateTable.from_dict({"temperature_ev": [1, 2], "coefficients": [2, 4], "units": "s^-1"}, 1)
    third = RateTable.from_dict({"temperature_ev": [1, 2], "coefficients": [0, 4], "units": "m^6/s"}, 3)
    assert first.evaluate(1.5) == pytest.approx(math.sqrt(8))
    assert third.evaluate(1.5) == pytest.approx(2)
    assert first.coefficients == (2, 4)
    with pytest.raises(ValueError, match="単位"):
        RateTable.from_dict({"temperature_ev": [1, 2], "coefficients": [2, 4]}, 1)
    with pytest.raises(ValueError, match="専用"):
        RateTable.from_dict({"temperature_ev": [1, 2], "coefficients_m3_s": [2, 4], "units": "m^6/s"}, 3)


@pytest.mark.parametrize("defect,expected_message", [
    ("elements_missing", "全粒子種"),
    ("fractional_elements", "整数"),
    ("gas_elements", "元素"),
    ("gas_charge", "電荷"),
    ("rate_units", "単位"),
    ("rate_negative", "有限値"),
    ("rate_infinite", "有限値"),
    ("missing_source", "出典"),
    ("nan_energy", "有限値"),
    ("missing_range", "temperature_range_ev"),
    ("fourth_order", "次数"),
    ("wall_elements", "元素"),
    ("wall_source", "wall_source"),
    ("wall_unknown", "未定義"),
    ("wall_fraction_nan", "有限値"),
])
def test_invalid_chemistry_is_rejected(defect, expected_message):
    data = _network_data()
    reaction = data["reactions"][0]
    atomic_oxygen = data["species"][3]
    if defect == "elements_missing":
        data["species"][1].pop("elements")
    elif defect == "fractional_elements":
        data["species"][1]["elements"]["O"] = 1.5
    elif defect == "gas_elements":
        reaction["products"]["O"] = 1
    elif defect == "gas_charge":
        reaction["products"]["e"] = 1
    elif defect == "rate_units":
        reaction["rate"]["units"] = "m^6/s"
    elif defect == "rate_negative":
        reaction["rate"]["coefficient"] = -1
    elif defect == "rate_infinite":
        reaction["rate"]["coefficient"] = math.inf
    elif defect == "missing_source":
        reaction.pop("source")
    elif defect == "nan_energy":
        reaction["electron_energy_loss_ev"] = math.nan
    elif defect == "missing_range":
        data.pop("temperature_range_ev")
    elif defect == "fourth_order":
        data["reactions"] = [_reaction("invalid", {"O": 4}, {"O2": 2}, 1, 3)]
    elif defect == "wall_elements":
        atomic_oxygen.update(wall_products={"O2": 1}, wall_source=TEST_SOURCE)
    elif defect == "wall_source":
        atomic_oxygen.update(wall_products={"O2": .5})
    elif defect == "wall_unknown":
        atomic_oxygen.update(wall_products={"undefined": 1}, wall_source=TEST_SOURCE)
    elif defect == "wall_fraction_nan":
        atomic_oxygen.update(wall_products={"O2": math.nan}, wall_source=TEST_SOURCE)
    with pytest.raises(ValueError, match=expected_message):
        ReactionNetwork(data, "O2")


@pytest.mark.parametrize("value", [-1, math.nan, math.inf])
def test_invalid_densities_and_wall_coefficients_are_rejected(value):
    model = ReactionNetwork(_network_data(), "O2")
    densities = _densities()
    densities["O"] = value
    with pytest.raises(ValueError, match="密度"):
        model.source_terms(densities, 3)
    with pytest.raises(ValueError, match="壁損失"):
        model.wall_source_terms(_densities(), {"O": value})


def test_constant_rate_does_not_accept_an_expression_or_temperature_table():
    data = _network_data()
    data["reactions"][0]["rate"]["type"] = "expression"
    with pytest.raises(ValueError, match="type"):
        ReactionNetwork(data, "O2")
    data = copy.deepcopy(_network_data())
    data["reactions"][0]["rate"]["temperature_ev"] = [1, 7]
    with pytest.raises(ValueError, match="同時"):
        ReactionNetwork(data, "O2")
    rate = ConstantRate.from_dict({"coefficient": 2, "units": "s^-1"}, 1)
    assert rate.evaluate(1) == rate.evaluate(7) == 2
    with pytest.raises(ValueError, match="温度"):
        rate.evaluate(math.nan)

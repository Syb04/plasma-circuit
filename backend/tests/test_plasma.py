"""Physics and real-solver checks, not accuracy claims for an experiment."""
from __future__ import annotations

import copy
import json
import math
from dataclasses import replace

import numpy as np
import pytest

from app.plasma import CCPSettings, argon_balances, execute_plasma, sheath_charge, solve_ccp
from app.plasma_models import ReactionNetwork, RateTable, argon_rates


def test_reference_geometry_and_drude_density_scaling():
    settings = CCPSettings()
    assert settings.cathode_area == pytest.approx(math.pi*.15**2)
    assert settings.anode_area/settings.cathode_area == 5
    assert settings.volume == pytest.approx(.0035342917352885173)
    doubled = replace(settings, electron_density_m3=2*settings.electron_density_m3)
    assert doubled.inductance == pytest.approx(settings.inductance/2)
    assert doubled.resistance/settings.resistance == pytest.approx(.5)
    assert settings.neutral_density == pytest.approx(3.218833278166041e20)


def test_sheath_charge_is_monotonic_and_has_positive_differential_capacitance():
    voltages = np.linspace(-500, 20, 5001)
    charge = sheath_charge(voltages, .07, 5e15, 3)
    assert np.isfinite(charge).all()
    assert (np.diff(charge) > 0).all()
    # A no-memory voltage excursion has zero net transported charge.
    sequence = np.array([-30, -100, -400, -100, -30])
    assert np.diff(sheath_charge(sequence, .07, 5e15)).sum() == pytest.approx(0, abs=1e-20)


def test_argon_rates_positive_and_loss_scales_with_density():
    settings = CCPSettings()
    rates = argon_rates(3)
    assert 0 < rates['ionization'] < rates['excitation']
    b1 = argon_balances(settings, 10)
    b2 = argon_balances(replace(settings, electron_density_m3=2e16), 20)
    for name in b1:
        assert b2[name] == pytest.approx(2*b1[name])


def test_rate_table_never_extrapolates_and_respects_zero_threshold():
    positive = RateTable((1, 2, 3), (1e-16, 1e-14, 1e-12))
    assert positive.evaluate(1.5) == pytest.approx(1e-15)
    threshold = RateTable((1, 2), (0, 2e-15))
    assert threshold.evaluate(1.5) == pytest.approx(1e-15)
    with pytest.raises(ValueError, match='適用範囲'):
        positive.evaluate(.9)


def _numerical_reaction_fixture():
    """Invented rates solely for checking stoichiometry, never shipped as chemistry."""
    return {
        'gas': 'O2', 'name': 'synthetic charge-accounting test only',
        'version': 'test', 'source': 'synthetic numerical unit test, no physics applicability',
        'species': [
            {'name':'O2', 'charge':0, 'mass_amu':32, 'reservoir':True},
            {'name':'O2+', 'charge':1, 'mass_amu':32},
            {'name':'O-', 'charge':-1, 'mass_amu':16},
            {'name':'O', 'charge':0, 'mass_amu':16},
        ],
        'reactions': [
            {'name':'ionization', 'reactants':{'e':1,'O2':1}, 'products':{'e':2,'O2+':1},
             'rate':{'temperature_ev':[1,7],'coefficients_m3_s':[1e-15,1e-15]},
             'source':'synthetic numerical test', 'electron_energy_loss_ev':12},
            {'name':'attachment', 'reactants':{'e':1,'O2':1}, 'products':{'O-':1,'O':1},
             'rate':{'temperature_ev':[1,7],'coefficients_m3_s':[1e-16,1e-16]},
             'source':'synthetic numerical test', 'electron_energy_loss_ev':1},
        ],
    }


def test_electronegative_network_charge_particle_and_energy_accounting():
    model = ReactionNetwork(_numerical_reaction_fixture(), 'O2')
    densities = {'O2':1e20, 'O2+':2e16, 'O-':1e16, 'O':1e17}
    assert model.quasineutral_electron_density(densities) == 1e16
    rates, power = model.source_terms(densities, 3)
    assert rates['O2+'] == pytest.approx(1e21)
    assert rates['O-'] == pytest.approx(1e20)
    assert rates['O'] == pytest.approx(1e20)
    assert power > 0
    broken = copy.deepcopy(_numerical_reaction_fixture())
    broken['reactions'][0]['products']['e'] = 1
    with pytest.raises(ValueError, match='電荷'):
        ReactionNetwork(broken, 'O2')


def test_template_guard_and_no_unprovided_molecular_chemistry():
    with pytest.raises(ValueError, match='専用'):
        execute_plasma({'components':[]}, {'kind':'ccp'})
    template = {'parameters':{'builtin_ccp_template':1}, 'components':[], 'wires':[]}
    with pytest.raises(ValueError, match='reaction_model'):
        execute_plasma(template, {'kind':'global','settings':{'gas':'O2'}})
    template['components'] = [{'kind':'R'}]
    with pytest.raises(ValueError, match='追加'):
        execute_plasma(template, {'kind':'ccp'})


def test_particle_only_oxygen_data_cannot_be_used_as_complete_energy_model():
    from app.oxygen import oxygen_network_data
    template = {'parameters':{'builtin_ccp_template':1}, 'components':[], 'wires':[]}
    with pytest.raises(ValueError, match='電子エネルギー収支'):
        execute_plasma(template, {'kind':'global', 'settings':{
            'gas':'O2', 'reaction_model':oxygen_network_data()}})


def test_fixed_ccp_real_solver_current_balance_and_positive_heating():
    result = solve_ccp(CCPSettings())
    assert result['converged']
    assert -250 < result['summary']['dc_self_bias_v'] < 0
    assert result['summary']['electron_heating_w'] > 0
    assert abs(result['diagnostics']['mean_electrode_current_a']) < result['diagnostics']['dc_current_tolerance_a']
    assert result['diagnostics']['periodic_current_relative_error'] < .02
    assert all(np.isfinite(signal['values']).all() for signal in result['signals'])
    assert 'Ccharge_c' in result['netlist']
    assert result['solver']['ngspice'] != 'unknown'
    assert result['diagnostics']['rf_power_balance_relative_error'] < .01
    assert result['diagnostics']['periodic_sheath_charge_relative_error'] < .02
    assert any(signal['name'] == 'V(metal_minus_plasma_cathode)' for signal in result['signals'])
    json.dumps(result, allow_nan=False)


def test_argon_global_real_solver_closes_particle_and_electron_energy_balance():
    template = {'parameters':{'builtin_ccp_template':1}, 'components':[], 'wires':[]}
    result = execute_plasma(template, {'kind':'global','settings':{'gas':'Ar'}})
    assert result['converged']
    balance = result['diagnostics']['global_balances']
    assert abs(balance['particle_residual_m3_s'])/balance['particle_source_m3_s'] < 1e-8
    assert abs(balance['energy_residual_w'])/balance['electron_energy_loss_w'] < .01
    assert 1e12 < result['summary']['electron_density_m3'] < 1e19
    assert 1 < result['summary']['electron_temperature_ev'] < 7
    assert result['summary']['electron_density_m3'] != CCPSettings().electron_density_m3
    assert result['model_metadata']['chemistry']['status'] == 'experimental_reduced_model_unvalidated'
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('location', ['charge','reactant','product'])
def test_fractional_chemistry_coefficients_are_rejected(location):
    data = _numerical_reaction_fixture()
    if location == 'charge':
        data['species'][1]['charge'] = 1.9
    elif location == 'reactant':
        data['reactions'][0]['reactants']['e'] = 1.9
    else:
        data['reactions'][0]['products']['e'] = 2.9
    with pytest.raises(ValueError, match='整数'):
        ReactionNetwork(data, 'O2')

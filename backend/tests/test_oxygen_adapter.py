"""Power-definition, species and RF/chemistry wall-current adapter checks."""
import json
import math

import pytest

from app.oxygen_adapter import execute_oxygen, oxygen_inputs, oxygen_rf_settings, rf_power_budget, rf_secondary_yield, solve_oxygen_absorbed
from app.plasma_models import ELEMENTARY_CHARGE as E, ELECTRON_MASS


@pytest.fixture
def settings():
    return {"gas": "O2", "power_mode": "prescribed_absorbed", "absorbed_power_w": 500,
        "gas_temperature_k": 600, "wall_edge_factor": .2, "cathode_diameter_m": .304,
        "gap_m": .076, "diffusion_o_m2_s": 1.2, "diffusion_o2_m2_s": .84,
        "electron_momentum_nu_s": {"O": 1e7, "O2": 1e7}}


def test_prescribed_power_is_total_not_electron_heating_and_web_tables_are_complete(settings):
    result = execute_oxygen(settings)
    assert result["converged"]
    assert result["summary"]["total_plasma_absorbed_power_w"] == 500
    assert "electron_heating_w" not in result["summary"]
    assert result["summary"]["reduced_energy_loss_w"] == pytest.approx(500, rel=1e-6)
    chemistry = result["diagnostics"]["oxygen_solution"]
    assert len(chemistry["densities_m3"]) == 11
    assert len(result["tables"][0]["rows"]) == 11
    assert len(result["tables"][2]["rows"]) == 5
    assert chemistry["metadata"]["source_values"]["implemented_reaction_count"] == 48
    assert "not all 48 reaction energies" in chemistry["metadata"]["closures"]["energy"]
    json.dumps(result, allow_nan=False)


def test_missing_prescribed_power_and_out_of_domain_candidate_are_not_success(settings):
    broken = dict(settings)
    del broken["absorbed_power_w"]
    with pytest.raises(ValueError, match="total absorbed_power_w"):
        execute_oxygen(broken)
    failed = execute_oxygen(settings | {"pressure_pa": .133322368, "diffusion_o_m2_s": 8.8, "diffusion_o2_m2_s": 6.3})
    assert not failed["converged"]
    assert not failed["diagnostics"]["oxygen_solution"]["source_domain_valid"]
    assert failed["summary"]["electron_temperature_ev"] <= 4.5


def test_imposed_rf_ion_acceleration_replaces_dc_sheath_energy_once(settings):
    solved = solve_oxygen_absorbed(settings, 500, ion_acceleration_power_w=100)
    assert solved["success"]
    n, te, wall = solved["densities_m3"], solved["temperature_ev"], solved["wall_rates_s"]
    current = E*solved["volume_m3"]*(n["O+"]*wall["k50"]+n["O2+"]*wall["k51"])
    assert solved["loss_w"]["ion_wall"] == pytest.approx(100+.5*te*current)
    electron_side = sum(solved["loss_w"][name] for name in ("ionization", "excitation", "elastic", "electron_wall"))+.5*te*current
    assert electron_side == pytest.approx(400, rel=1e-6)
    assert solved["plasma_potential_v"] == pytest.approx(100/current)
    assert solved["loss_w"]["total"] == pytest.approx(500, rel=1e-6)


def test_secondary_wall_escape_and_effective_rf_areas_keep_current_and_energy_consistent(settings):
    configured = settings | {"secondary_electron_yield_cathode": .2, "secondary_electron_yield_anode": .1,
        "wall_loss_area_m2": 9}
    solved = solve_oxygen_absorbed(configured, 500, ion_acceleration_power_w=100)
    assert solved["success"]
    parsed = oxygen_rf_settings(configured, solved)
    wall_current = E*solved["volume_m3"]*solved["electron_wall_loss_m3_s"]
    assert parsed.ion_wall_current_density_a_m2*(parsed.cathode_area+parsed.anode_area) == pytest.approx(wall_current)
    assert parsed.loss_area == 9
    assert solved["loss_w"]["electron_wall"] == pytest.approx(
        2*solved["temperature_ev"]*wall_current*(1+rf_secondary_yield(configured)))


def test_rf_fixed_point_passes_ne_te_species_and_wall_current_to_circuit(settings):
    trials = []

    def constant_rf_for_energy_accounting(parsed):
        trials.append(parsed)
        return {"kind": "ccp", "converged": True, "summary": {"electron_heating_w": 400,
            "ion_acceleration_power_w": 100, "conductive_sheath_power_w": 80, "electrode_absorbed_power_w": 480},
            "axis": {"name": "time", "unit": "s", "values": [0, 1]}, "signals": [], "tables": [],
            "logs": [], "solver": {"purpose": "synthetic power-interface conservation test"},
            "netlist": "", "model_metadata": {}, "diagnostics": {}}

    result = execute_oxygen(settings | {"power_mode": "rf_coupled"}, constant_rf_for_energy_accounting)
    assert result["converged"]
    assert len(trials) >= 2
    assert result["summary"]["total_plasma_absorbed_power_w"] == 480
    assert result["summary"]["electrode_absorbed_power_w"] == 480
    assert result["diagnostics"]["rf_oxygen_power_budget"]["electron_retarding_sheath_work_w"] == 20
    chemistry = result["diagnostics"]["oxygen_solution"]
    n, kw = chemistry["densities_m3"], chemistry["wall_rates_s"]
    wall_current = E*chemistry["volume_m3"]*(n["O+"]*kw["k50"]+n["O2+"]*kw["k51"])
    assert trials[-1].ion_wall_current_density_a_m2*(trials[-1].cathode_area+trials[-1].anode_area) == pytest.approx(wall_current, rel=1e-5)
    assert trials[-1].neutral_species_densities_m3 == pytest.approx({"O": n["O"], "O2": n["O2"]}, rel=1e-5)
    assert trials[-1].momentum_collision_frequency_hz == 2e7
    json.dumps(result, allow_nan=False)


def test_configured_surfaces_override_source_candidates_with_documented_weighting(settings):
    entries = {role: {"material": "Si", "state": "user assumption test", "temperature_k": 300,
        "gamma_o": gamma, "gamma_metastable": .01, "secondary_electron_yield": 0}
        for role, gamma in (("cathode", .1), ("anode", .2), ("wall", .3))}
    configured = settings | {"surface_parameters": entries}
    inputs = oxygen_inputs(configured, 500)
    assert .1 < inputs.gamma_o < .3
    assert inputs.gamma_meta == pytest.approx(.01)
    result = execute_oxygen(configured)
    assert result["converged"]
    assert result["model_metadata"]["chemistry"]["surface_parameters"]["configured"]
    assert "User supplied" in result["model_metadata"]["chemistry"]["closures"]["surface"]


def test_nonconverged_rf_cannot_be_promoted_to_converged_chemistry(settings):
    def unresolved_rf(parsed):
        return {"converged": False, "summary": {"electron_heating_w": 400, "ion_acceleration_power_w": 100},
            "tables": [], "logs": [], "model_metadata": {}, "diagnostics": {}}
    assert not execute_oxygen(settings | {"power_mode": "rf_coupled"}, unresolved_rf)["converged"]


def test_circuit_power_ledger_accounts_for_electron_retarding_work_and_secondary_transfer():
    budget = rf_power_budget({"electron_heating_w": 430, "ion_acceleration_power_w": 100,
        "secondary_electron_acceleration_power_w": 30, "conductive_sheath_power_w": 110,
        "electrode_absorbed_power_w": 510})
    assert budget["total_absorbed_w"] == 510
    assert budget["electron_retarding_sheath_work_w"] == 20
    assert budget["terminal_port_minus_mapped_total_w"] == 0
    assert budget["electron_plus_ion_channel_sum_w"] == 530


def test_supplied_cross_section_transport_tracks_solved_composition_and_temperature(settings):
    tables = [{"id": name+"_momentum", "target": name, "process": "momentum_transfer",
        "energy_ev": [0, 300], "sigma_m2": [sigma, sigma],
        "source": {"title": "Analytic numerical fixture; not measured oxygen data"}}
        for name, sigma in (("O", 1e-20), ("O2", 2e-20))]
    solved = solve_oxygen_absorbed(settings | {"electron_transport": {
        "mode": "cross_section_eedf", "cross_sections": tables}}, 500)
    assert solved["success"]
    report = solved["metadata"]["electron_transport"]
    mean_speed = math.sqrt(8*E*solved["temperature_ev"]/(math.pi*ELECTRON_MASS))
    for name, sigma in (("O", 1e-20), ("O2", 2e-20)):
        assert report["target_collision_frequencies_s"][name] == pytest.approx(
            solved["densities_m3"][name]*sigma*mean_speed, rel=1e-6)
    assert report["neutral_densities_m3"] == pytest.approx({"O": solved["densities_m3"]["O"], "O2": solved["densities_m3"]["O2"]})


def test_real_ngspice_rf_oxygen_closes_total_power_with_explicit_numeric_assumptions(settings):
    # Large explicit nu is a numerical applicability case, not measured oxygen
    # transport or a fitted validation case. It permits this Ohmic-only closure.
    result = execute_oxygen(settings | {"power_mode": "rf_coupled", "frequency_hz": 13.56e6,
        "rf_peak_voltage": 500, "pressure_pa": 6.6661184, "cycles": 32, "points_per_cycle": 128,
        "momentum_collision_frequency_hz": 1e10, "electron_momentum_nu_s": {"O": 5e9, "O2": 5e9}})
    assert result["converged"]
    summary = result["summary"]
    assert 1 < summary["electron_temperature_ev"] < 4.5
    assert summary["total_plasma_absorbed_power_w"] == pytest.approx(
        summary["electron_heating_w"]+summary["conductive_sheath_power_w"]-summary["secondary_electron_acceleration_power_w"])
    assert result["diagnostics"]["rf_oxygen_power_budget"]["electron_retarding_sheath_work_w"] > 0
    assert summary["reduced_energy_loss_w"] == pytest.approx(summary["total_plasma_absorbed_power_w"], rel=1e-6)
    assert result["diagnostics"]["oxygen_balances"]["checks"]["oxygen_atoms"]
    assert result["solver"]["ngspice"] != "unknown"

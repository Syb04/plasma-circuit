import math

import numpy as np
import pytest

from app.electron_transport import integrate_cross_section, resolve_electron_transport
from app.plasma_models import ELEMENTARY_CHARGE, ELECTRON_MASS


def table(sigma=2e-20, maximum=300):
    return {"id": "O2_momentum", "target": "O2", "process": "momentum_transfer",
            "energy_ev": [0, maximum], "sigma_m2": [sigma, sigma], "source": {"title": "Analytic test fixture, not measured data"}}


def test_constant_cross_section_maxwellian_analytic_mean_speed_and_si_frequency():
    te, sigma, density = 3.0, 2e-20, 1e20
    expected = sigma * math.sqrt(8 * ELEMENTARY_CHARGE * te / (math.pi * ELECTRON_MASS))
    direct = integrate_cross_section(table(sigma), electron_temperature_ev=te)
    assert direct["rate_coefficient_m3_s"] == pytest.approx(expected, rel=2e-13)
    assert direct["eedf"]["normalization"] == 1
    assert direct["eedf"]["mean_energy_ev"] == 4.5
    report = resolve_electron_transport({"mode": "cross_section_eedf", "cross_sections": [table(sigma)]},
                                        electron_temperature_ev=te, neutral_densities_m3={"O2": density}, explicit_nu_s=123)
    assert report["momentum_collision_frequency_s"] == pytest.approx(density * expected, rel=2e-13)
    assert report["rate_coefficients_m3_s"]["O2_momentum"] == pytest.approx(expected, rel=2e-13)


def test_uniform_energy_pdf_has_analytic_sqrt_energy_average():
    maximum, sigma = 12.0, 1e-20
    eedf = {"mode": "tabulated", "energy_ev": [0, maximum], "probability_per_ev": [1 / maximum, 1 / maximum],
            "source": "Uniform finite-support analytic test"}
    result = integrate_cross_section(table(sigma, maximum), electron_temperature_ev=3, eedf=eedf)
    expected = sigma * math.sqrt(2 * ELEMENTARY_CHARGE / ELECTRON_MASS) * 2 * math.sqrt(maximum) / 3
    assert result["rate_coefficient_m3_s"] == pytest.approx(expected, rel=2e-5)
    assert result["eedf"]["mean_energy_ev"] == pytest.approx(maximum / 2)
    assert result["coverage"]["omitted_probability"] == 0


def test_rate_probability_and_tail_coverage_are_explicit():
    with pytest.raises(ValueError, match="tail"):
        integrate_cross_section(table(maximum=10), electron_temperature_ev=3)
    tail = integrate_cross_section(table(maximum=60), electron_temperature_ev=3)
    assert 0 < tail["coverage"]["omitted_probability"] < 1e-6
    assert tail["coverage"]["unknown_tail_rate_not_bounded"]
    assert not tail["coverage"]["extrapolation"]
    bad_eedf = {"mode": "tabulated", "energy_ev": [0, 10], "probability_per_ev": [.2, .2], "source": "test"}
    with pytest.raises(ValueError, match="integrate to one"):
        integrate_cross_section(table(), electron_temperature_ev=3, eedf=bad_eedf)
    with pytest.raises(ValueError, match="source"):
        integrate_cross_section(dict(table(), source=None), electron_temperature_ev=3)


def test_no_gap_or_extrapolation_and_declared_below_threshold_zero():
    imported = dict(table(), energy_ev=[5, 300], sigma_m2=[0, 1e-20])
    with pytest.raises(ValueError, match="start at zero"):
        integrate_cross_section(imported, electron_temperature_ev=3)
    declared = dict(imported, threshold_ev=5)
    result = integrate_cross_section(declared, electron_temperature_ev=3)
    assert result["rate_coefficient_m3_s"] > 0
    cold_pdf = {"mode": "tabulated", "energy_ev": [0, 2], "probability_per_ev": [.5, .5], "source": "test"}
    assert integrate_cross_section(declared, electron_temperature_ev=3, eedf=cold_pdf)["rate_coefficient_m3_s"] == 0
    broad_pdf = {"mode": "tabulated", "energy_ev": [0, 400], "probability_per_ev": [1 / 400, 1 / 400], "source": "test"}
    with pytest.raises(ValueError, match="support"):
        integrate_cross_section(declared, electron_temperature_ev=3, eedf=broad_pdf)


def test_all_nonzero_targets_require_momentum_data_without_duplicate_totals():
    config = {"mode": "cross_section_eedf", "cross_sections": [table()]}
    with pytest.raises(ValueError, match="Missing momentum"):
        resolve_electron_transport(config, electron_temperature_ev=3, neutral_densities_m3={"O2": 1e20, "O": 1e19}, explicit_nu_s=1)
    with pytest.raises(ValueError, match="one total"):
        resolve_electron_transport(dict(config, cross_sections=[table(), dict(table(), id="duplicate")]),
                                   electron_temperature_ev=3, neutral_densities_m3={"O2": 1e20}, explicit_nu_s=1)
    explicit = resolve_electron_transport(None, electron_temperature_ev=3, neutral_densities_m3={"O2": 1e20}, explicit_nu_s={"O": 0, "O2": 2e7})
    assert explicit["momentum_collision_frequency_s"] == 2e7
    assert not explicit["data_derived"]


@pytest.mark.parametrize("changes", [{"sigma_m2": [-1, 0]}, {"energy_ev": [2, 2]}, {"energy_ev": [np.nan, 1]}, {"sigma_m2": [0]}])
def test_bad_imported_arrays_are_rejected(changes):
    with pytest.raises(ValueError):
        integrate_cross_section(dict(table(), **changes), electron_temperature_ev=3)

import math

import numpy as np
import pytest

from app.rf_analysis import analyze_rf


def waveform(v, i, t):
    return {"axis": {"unit": "s", "values": t.tolist()}, "signals": [
        {"name": "V(cathode)", "values": v.tolist()}, {"name": "I(electrode)", "values": i.tolist()},
        {"name": "V(source_port)", "values": v.tolist()}, {"name": "I(source_port)", "values": i.tolist()}]}


def test_adaptive_samples_recover_rl_impedance_and_directional_power():
    f = 40e6
    uniform = np.linspace(0, 2/f, 12001)
    t = uniform + .1*(uniform[1]-uniform[0])*np.sin(np.arange(len(uniform)))
    t[0], t[-1] = 0, 2/f
    phase = math.atan2(30, 40)
    v, i = 100*np.cos(2*math.pi*f*t), 2*np.cos(2*math.pi*f*t-phase)
    result = analyze_rf(waveform(v, i, t), {"frequency_hz": f, "rf_reference_impedance_ohm": 50})
    electrode, source = result["measurement_planes"]
    assert electrode["impedance_ohm"]["real"] == pytest.approx(40, rel=1e-5)
    assert electrode["impedance_ohm"]["imag"] == pytest.approx(30, rel=1e-5)
    assert electrode["mean_power_w"] == pytest.approx(80, rel=1e-5)
    assert source["forward_power_w"]-source["reflected_power_w"] == pytest.approx(80, rel=1e-5)
    assert source["directional_power_balance_residual_w"] == pytest.approx(0, abs=1e-10)


def test_dc_and_third_harmonic_power_are_not_confused_with_fundamental():
    f = 1e6
    t = np.linspace(0, 2/f, 8193)
    p = 2*math.pi*f*t
    v, i = 5+100*np.cos(p)+20*np.cos(3*p), .2+2*np.cos(p)+.4*np.cos(3*p)
    r = analyze_rf(waveform(v, i, t), {"frequency_hz": f})["measurement_planes"][0]
    assert r["mean_power_w"] == pytest.approx(105)
    assert r["dc_power_w"] == pytest.approx(1)
    assert r["fundamental_power_w"] == pytest.approx(100)
    assert r["harmonics"][2]["power_w"] == pytest.approx(4)
    assert r["current_thd"] == pytest.approx(.2)


def test_zero_current_is_unavailable_impedance_not_infinity():
    t = np.linspace(0, 2e-6, 1025)
    r = analyze_rf(waveform(np.sin(2*np.pi*1e6*t), np.zeros_like(t), t), {"frequency_hz": 1e6})
    assert r["measurement_planes"][0]["impedance_ohm"]["real"] is None


def test_nullable_optional_fundamental_uses_saved_drive_period():
    f = 1e6
    t = np.linspace(0, 2/f, 2049)
    result = waveform(100*np.cos(2*np.pi*f*t), 2*np.cos(2*np.pi*f*t), t)
    result["model_metadata"] = {"rf_drive": {"fundamental_frequency_hz": f}}
    report = analyze_rf(result, {"fundamental_frequency_hz": None, "frequency_hz": 2*f})
    assert report["frequency_hz"] == f
    assert report["measurement_planes"][0]["mean_power_w"] == pytest.approx(100)
    result.pop("model_metadata")
    assert analyze_rf(result, {"fundamental_frequency_hz": None, "frequency_hz": f})["frequency_hz"] == f


def test_incomplete_period_and_nonmonotonic_samples_rejected():
    t = np.linspace(0, .5e-6, 33)
    with pytest.raises(ValueError, match="基本周期"):
        analyze_rf(waveform(t, t, t), {"frequency_hz": 1e6})
    t[4] = t[3]
    with pytest.raises(ValueError, match="単調"):
        analyze_rf(waveform(t, t, t), {"frequency_hz": 1e6})

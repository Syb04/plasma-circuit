"""Large-signal RF measurements on retained adaptive-time ngspice samples.

Phasors are peak, with x(t)=Re(X exp(j wt)); positive current enters the load.
Directional power is defined only at a plane with an explicit real Z0.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.integrate import trapezoid


def _mean(t: np.ndarray, values: np.ndarray) -> float:
    return float(trapezoid(values, t) / (t[-1] - t[0]))


def analyze_rf(result: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
    axis = result.get("axis") or {}
    if axis.get("unit") != "s" or len(axis.get("values", [])) < 8:
        return None
    configured_frequency = settings.get("fundamental_frequency_hz")
    if configured_frequency is None:
        configured_frequency = settings.get("frequency_hz", 0)
    drive = result.get("model_metadata", {}).get("rf_drive", {})
    frequency = float(drive.get("fundamental_frequency_hz") or configured_frequency or 0)
    if not math.isfinite(frequency) or frequency <= 0:
        return None
    t = np.asarray(axis["values"], dtype=float)
    if not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
        raise ValueError("RF解析の時刻は有限値の単調増加配列にしてください")
    cycles = min(10, int(math.floor((t[-1] - t[0]) * frequency + 1e-7)))
    if cycles < 1:
        raise ValueError("RF解析には基本周期を1周期以上保存してください")
    start = t[-1] - cycles / frequency
    window = np.concatenate(([start], t[(t > start) & (t < t[-1])], [t[-1]]))
    signals = {s["name"]: np.asarray(s["values"], dtype=float) for s in result.get("signals", [])}
    reference = settings.get("rf_reference_impedance_ohm")
    external = settings.get("external_circuit") or {}
    reference = drive.get("reference_impedance_ohm", reference or external.get("source_resistance_ohm"))
    candidates = [("electrode", "V(cathode)", "I(electrode)", None),
                  ("source", "V(source_port)", "I(source_port)", reference)]
    if settings.get("rf_voltage_signal") and settings.get("rf_current_signal"):
        candidates.append(("custom", settings["rf_voltage_signal"], settings["rf_current_signal"], reference))
    requested = int(settings.get("rf_harmonics", 12))
    if not 1 <= requested <= 64:
        raise ValueError("高調波次数は1〜64で指定してください")
    max_step = float(np.max(np.diff(window)))
    harmonic_limit = max(1, int(math.floor(1 / (4 * frequency * max_step))))
    order_limit = min(requested, harmonic_limit)
    planes: list[dict[str, Any]] = []
    for name, voltage_name, current_name, z0 in candidates:
        if voltage_name not in signals or current_name not in signals:
            continue
        for signal_name in (voltage_name, current_name):
            if len(signals[signal_name]) != len(t) or not np.isfinite(signals[signal_name]).all():
                raise ValueError(f"RF信号 {signal_name} の長さ・有限値を確認してください")
        v, i = (np.interp(window, t, signals[key]) for key in (voltage_name, current_name))
        vdc, idc = _mean(window, v), _mean(window, i)
        vrms, irms = math.sqrt(_mean(window, v*v)), math.sqrt(_mean(window, i*i))
        power = _mean(window, v*i)
        harmonics, v1, i1 = [], 0j, 0j
        for order in range(1, order_limit + 1):
            phase = np.exp(-2j * math.pi * frequency * order * (window - start))
            vp = 2 * trapezoid((v-vdc) * phase, window) / (window[-1]-window[0])
            ip = 2 * trapezoid((i-idc) * phase, window) / (window[-1]-window[0])
            if order == 1:
                v1, i1 = complex(vp), complex(ip)
            harmonics.append({"order": order, "frequency_hz": frequency*order,
                              "voltage_peak_v": float(abs(vp)), "current_peak_a": float(abs(ip)),
                              "voltage_phase_deg": float(np.angle(vp, deg=True)),
                              "current_phase_deg": float(np.angle(ip, deg=True)),
                              "power_w": float((vp * np.conjugate(ip)).real / 2)})
        impedance = v1 / i1 if abs(i1) > max(1e-15, irms*1e-10) else None
        phase_deg = float(np.angle(impedance, deg=True)) if impedance is not None else None
        harmonic_power = sum(row["power_w"] for row in harmonics)
        thd = math.sqrt(sum(row["current_peak_a"]**2 for row in harmonics[1:])) / abs(i1) if abs(i1) > 1e-15 else None
        plane: dict[str, Any] = {
            "name": name, "voltage_signal": voltage_name, "current_signal": current_name,
            "reference_impedance_ohm": float(z0) if z0 is not None else None,
            "impedance_ohm": {"real": float(impedance.real), "imag": float(impedance.imag)} if impedance is not None else {"real": None, "imag": None},
            "phase_deg": phase_deg, "voltage_rms_v": vrms, "current_rms_a": irms,
            "mean_voltage_v": vdc, "mean_current_a": idc, "mean_power_w": power,
            "dc_power_w": vdc*idc, "fundamental_power_w": harmonics[0]["power_w"],
            "power_factor": power / (vrms*irms) if vrms*irms > 1e-20 else None,
            "current_thd": thd, "harmonics": harmonics,
            "truncated_harmonic_power_residual_w": power-vdc*idc-harmonic_power,
        }
        if z0 is not None:
            z0 = float(z0)
            if not math.isfinite(z0) or z0 <= 0:
                raise ValueError("RF参照インピーダンスは正の実数で指定してください")
            vf, vb = ((v-vdc) + z0*(i-idc))/2, ((v-vdc) - z0*(i-idc))/2
            forward, reflected = _mean(window, vf*vf)/z0, _mean(window, vb*vb)/z0
            plane.update({"forward_power_w": forward, "reflected_power_w": reflected,
                          "directional_power_balance_residual_w": forward-reflected-(power-vdc*idc),
                          "fundamental_forward_power_w": abs((v1+z0*i1)/2)**2/(2*z0),
                          "fundamental_reflected_power_w": abs((v1-z0*i1)/2)**2/(2*z0)})
        planes.append(plane)
    if not planes:
        return None
    return {"frequency_hz": frequency, "cycles_used": cycles, "voltage_definition": "peak-phasor",
            "measurement_planes": planes,
            "quality": {"integer_cycles": True, "requested_harmonics": requested,
                        "resolved_harmonics": order_limit, "maximum_sample_step_s": max_step,
                        "harmonic_resolution_limited": requested > order_limit},
            "assumptions": ["定常波形の整数基本周期を、保存された可変刻み上で積分する。",
                            "複素Zは大信号周期波形の基本波V1/I1。DC動作点の小信号ACとは異なる。",
                            "進行・反射電力は指定した実数Z0の測定面で、DC成分を除いた全波形から計算する。"]}


def attach_rf_analysis(result: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    diagnostic = analyze_rf(result, settings)
    if diagnostic is None:
        return result
    result["rf_diagnostics"] = diagnostic
    tables = result.setdefault("tables", [])
    for plane in diagnostic["measurement_planes"]:
        tables.append({"name": f"RF高調波 — {plane['name']}",
                       "columns": ["order", "frequency_hz", "voltage_peak_v", "current_peak_a", "power_w"],
                       "rows": [{key: row[key] for key in ("order", "frequency_hz", "voltage_peak_v", "current_peak_a", "power_w")} for row in plane["harmonics"]]})
    return result

"""Uniform TEM coax with passive, reference-frequency loss fits.

The skin-effect series RL fit preserves DC resistance. A Debye RC branch
matches dielectric admittance at the reference frequency without DC leakage.
Neither fit is a broadband material model. A common ideal shield reference
excludes the external common mode; both conductors' losses enter loop Z.
"""
from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, asdict

import numpy as np
from scipy.special import ive
from scipy.integrate import trapezoid

from .expressions import parse_si

MU0 = 1.25663706212e-6
EPS0 = 8.8541878128e-12
DEFAULT_COAX_PARAMETERS = {
    "inner_diameter_m": 1e-3, "shield_inner_diameter_m": 3.35e-3,
    "length_m": 1., "relative_permittivity": 2.1, "relative_permeability": 1.,
    "loss_tangent": 2e-4, "inner_resistivity_ohm_m": 1.724e-8,
    "shield_resistivity_ohm_m": 1.724e-8, "shield_thickness_m": .15e-3,
    "reference_frequency_hz": 40e6, "segments": 32,
}
COAX_NUMBERS = frozenset(DEFAULT_COAX_PARAMETERS)
MAX_TOTAL_COAX_SEGMENTS = 512


@dataclass(frozen=True)
class Coax:
    parameters: dict
    inductance_h_m: float
    capacitance_f_m: float
    dc_resistance_ohm_m: float
    rf_resistance_ohm_m: float
    internal_reactance_ohm_m: float
    skin_resistance_ohm_m: float
    skin_inductance_h_m: float
    dielectric_capacitance_f_m: float
    relaxation_capacitance_f_m: float
    relaxation_resistance_ohm_m: float

    @classmethod
    def parse(cls, values: dict) -> "Coax":
        if not isinstance(values, dict):
            raise ValueError("同軸ケーブルのパラメータはオブジェクトで指定してください")
        unknown = set(values) - COAX_NUMBERS
        if unknown:
            raise ValueError("同軸ケーブルの未対応パラメータ: " + ", ".join(sorted(unknown)))
        p = {}
        for key, default in DEFAULT_COAX_PARAMETERS.items():
            value = values.get(key, default)
            try:
                if isinstance(value, bool):
                    raise ValueError()
                p[key] = parse_si(value)
                if not math.isfinite(p[key]):
                    raise ValueError()
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"{key}は有限の数値で指定してください") from exc
        nonnegative = {"loss_tangent", "inner_resistivity_ohm_m", "shield_resistivity_ohm_m"}
        for key, value in p.items():
            if value < 0 or (value == 0 and key not in nonnegative):
                raise ValueError(f"{key}は{'非負' if key in nonnegative else '正'}の値で指定してください")
        if p["shield_inner_diameter_m"] <= p["inner_diameter_m"]:
            raise ValueError("シールド内径は内部導体直径より大きくしてください")
        if p["loss_tangent"] >= 1:
            raise ValueError("誘電正接は0以上1未満で指定してください")
        if not p["segments"].is_integer() or not 1 <= p["segments"] <= 256:
            raise ValueError("同軸ケーブルの分割数は1〜256の整数で指定してください")
        p["segments"] = int(p["segments"])
        try:
            a, b = p["inner_diameter_m"] / 2, p["shield_inner_diameter_m"] / 2
            logarithm = math.log(p["shield_inner_diameter_m"]) - math.log(p["inner_diameter_m"])
            l = MU0 * p["relative_permeability"] * logarithm / (2 * math.pi)
            c = 2 * math.pi * EPS0 * p["relative_permittivity"] / logarithm
            omega = 2 * math.pi * p["reference_frequency_hz"]
            ri = p["inner_resistivity_ohm_m"] / (math.pi * a * a)
            thickness = p["shield_thickness_m"]
            shield_area = math.pi * thickness * (2 * b + thickness)
            rs = p["shield_resistivity_ohm_m"] / shield_area
            zi = _solid_impedance(a, p["inner_resistivity_ohm_m"], omega, ri)
            zs = _shield_impedance(b, thickness, p["shield_resistivity_ohm_m"], omega, rs)
            z = zi + zs
            dc = ri + rs
            delta = max(0., z.real - dc)
            # A passive R || L section has Re/Im = omega*L/R. Match Z at f_ref.
            skin_r = delta + z.imag * z.imag / delta if delta > dc * 1e-12 else 0.
            skin_l = (delta * delta + z.imag * z.imag) / (omega * z.imag) if skin_r and z.imag > 0 else z.imag / omega
            loss_c = 2 * c * p["loss_tangent"]
            loss_r = 1 / (omega * loss_c) if loss_c else 0.
            cable = cls(p, l, c, dc, z.real, z.imag, skin_r, skin_l,
                        c * (1 - p["loss_tangent"]), loss_c, loss_r)
            numbers = [v for k, v in asdict(cable).items() if k != "parameters"]
            if not all(math.isfinite(v) and v >= 0 for v in numbers) or l <= 0 or c <= 0:
                raise ValueError()
            cable.metadata()  # Also reject overflowing derived propagation values.
            return cable
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            raise ValueError("同軸ケーブルの寸法・材料・周波数から有効な線路定数を計算できません") from exc

    def propagation(self) -> tuple[complex, complex]:
        omega = 2 * math.pi * self.parameters["reference_frequency_hz"]
        z = complex(self.rf_resistance_ohm_m, omega * self.inductance_h_m + self.internal_reactance_ohm_m)
        y = omega * self.capacitance_f_m * complex(self.parameters["loss_tangent"], 1)
        gamma, impedance = cmath.sqrt(z * y), cmath.sqrt(z / y)
        if not all(math.isfinite(v) for v in (gamma.real, gamma.imag, impedance.real, impedance.imag)):
            raise ValueError("線路定数の計算範囲を超えています")
        return gamma, impedance

    def metadata(self) -> dict:
        gamma, impedance = self.propagation()
        length, n = self.parameters["length_m"], self.parameters["segments"]
        velocity = 1 / math.sqrt(self.inductance_h_m * self.capacitance_f_m)
        phase = gamma.imag * length / n
        result = {"parameters": dict(self.parameters), "model": "passive TEM pi ladder with reference-frequency Foster/Debye loss fits",
            "nominal_impedance_ohm": math.sqrt(self.inductance_h_m / self.capacitance_f_m),
            "tem_velocity_m_s": velocity, "tem_delay_s": length / velocity,
            "inductance_h_m": self.inductance_h_m, "capacitance_f_m": self.capacitance_f_m,
            "dc_resistance_ohm_m": self.dc_resistance_ohm_m, "rf_resistance_ohm_m": self.rf_resistance_ohm_m,
            "internal_inductance_at_reference_h_m": self.internal_reactance_ohm_m / (2 * math.pi * self.parameters["reference_frequency_hz"]),
            "dielectric_conductance_at_reference_s_m": 2 * math.pi * self.parameters["reference_frequency_hz"] * self.capacitance_f_m * self.parameters["loss_tangent"],
            "reference_impedance_real_ohm": impedance.real, "reference_impedance_imag_ohm": impedance.imag,
            "attenuation_db_m": 20 / math.log(10) * gamma.real, "attenuation_db": 20 / math.log(10) * gamma.real * length,
            "reference_phase_rad_m": gamma.imag, "section_phase_rad": phase,
            "warnings": ["分割数を増やしてください。基準周波数で1区間の位相が0.2 radを超えています。"] if phase > .2 else [],
            "assumptions": ["シールド径は内径。均一な誘電体、非磁性の中実内部導体と平滑な円筒シールド。",
                "内部導体は円柱Bessel解、シールドはDC断面積を保つ平板表皮近似。編組・撚線・めっき・粗さは含まない。",
                "内部・シールドの抵抗と内部インダクタンスを差動ループへ合算。シールド両端は共通の理想基準。外部コモンモードは含まない。",
                "導体のDC抵抗と基準周波数の複素インピーダンス、誘電体の基準周波数の複素アドミタンスを受動回路で再現。",
                "損失の周波数依存は基準周波数に合わせた近似。広帯域・高調波・急峻なパルスでは材料モデルと分割数を検証する。"]}
        if not all(math.isfinite(value) for value in result.values() if isinstance(value, (int, float))):
            raise ValueError("同軸ケーブルの計算範囲を超えています")
        return result


def _solid_impedance(radius: float, rho: float, omega: float, dc: float) -> complex:
    if rho == 0:
        return 0j
    k = cmath.sqrt(1j * omega * MU0 / rho)
    x = k * radius
    if abs(x) < 1e-3:
        return complex(dc, omega * MU0 / (8 * math.pi))
    return rho * k / (2 * math.pi * radius) * ive(0, x) / ive(1, x)


def _shield_impedance(radius: float, thickness: float, rho: float, omega: float, dc: float) -> complex:
    if rho == 0:
        return 0j
    k = cmath.sqrt(1j * omega * MU0 / rho)
    effective_thickness = thickness * (1 + thickness / (2 * radius))
    x = k * effective_thickness
    if abs(x) < 1e-3:
        return complex(dc, omega * MU0 * effective_thickness / (6 * math.pi * radius))
    return rho * k / (2 * math.pi * radius) / cmath.tanh(x)


def stamp(cable: Coax, cid: str, terminals: list[str], initial: dict) -> list[str]:
    p1, n1, p2, n2 = terminals
    p = cable.parameters
    n, dx = p["segments"], p["length_m"] / p["segments"]
    tag = f"cx_{cid}"
    nodes = [f"{tag}_p{i}" for i in range(n + 1)]
    lines = [f"* COAX {cid}: uniform TEM; loss fit at {p['reference_frequency_hz']:.16g} Hz; {n} sections",
             f"V_coax_{cid}_in {p1} {nodes[0]} 0", f"V_coax_{cid}_out {p2} {nodes[-1]} 0",
             f"V_{tag}_shield {n2} {n1} 0"] if n1 != n2 else [
             f"* COAX {cid}: uniform TEM; loss fit at {p['reference_frequency_hz']:.16g} Hz; {n} sections",
             f"V_coax_{cid}_in {p1} {nodes[0]} 0", f"V_coax_{cid}_out {p2} {nodes[-1]} 0"]
    for i in range(n):
        previous = nodes[i]
        if cable.dc_resistance_ohm_m:
            next_node = f"{tag}_r{i}"
            lines.append(f"R_{tag}_dc{i} {previous} {next_node} {cable.dc_resistance_ohm_m * dx:.16g}")
            previous = next_node
        if cable.skin_inductance_h_m:
            next_node = f"{tag}_skin{i}"
            lines.append(f"L_{tag}_skin{i} {previous} {next_node} {cable.skin_inductance_h_m * dx:.16g}")
            if cable.skin_resistance_ohm_m:
                lines.append(f"R_{tag}_skin{i} {previous} {next_node} {cable.skin_resistance_ohm_m * dx:.16g}")
            previous = next_node
        lines.append(f"L_{tag}_{i} {previous} {nodes[i+1]} {cable.inductance_h_m * dx:.16g}")
    for i, node in enumerate(nodes):
        distance = dx * (.5 if i in {0, n} else 1.)
        bias = ((1 - i / n) * float(initial.get(p1, 0)) + i / n * float(initial.get(p2, 0)) - float(initial.get(n1, 0)))
        ic = f" IC={bias:.16g}" if initial else ""
        lines.append(f"C_{tag}_{i} {node} {n1} {cable.dielectric_capacitance_f_m * distance:.16g}{ic}")
        if cable.relaxation_capacitance_f_m:
            loss_node = f"{tag}_d{i}"
            lines += [f"R_{tag}_dielectric{i} {node} {loss_node} {cable.relaxation_resistance_ohm_m / distance:.16g}",
                      f"C_{tag}_dielectric{i} {loss_node} {n1} {cable.relaxation_capacitance_f_m * distance:.16g}{ic}"]
    return lines


def add_results(result: dict, document: dict, nets: dict, kind: str, periodic_time=None) -> None:
    """Publish terminal quantities; keep ladder implementation out of plots."""
    cables = {}
    for component in document.get("components", []):
        if str(component["kind"]).upper() != "COAX":
            continue
        cid = component["id"]
        cable = Coax.parse(component.get("parameters", {}))
        cables[cid] = cable.metadata()
        complex_mode = kind == "ac"
        x = np.asarray(result["x"])
        def vector(alias):
            alias = alias.lower()
            if alias == "0":
                return np.zeros(len(x), dtype=complex if complex_mode else float)
            if complex_mode:
                v = result["complex_vectors"][alias]
                return np.asarray(v["real"]) + 1j * np.asarray(v["imag"])
            return np.asarray(result["vectors"][alias])
        powers = []
        sample_indices = np.unique(np.linspace(0, len(x)-1, min(len(x), 20000), dtype=int))
        def samples(values):
            if periodic_time is not None:
                return np.interp(periodic_time, x, values).tolist()
            return values[sample_indices].tolist()
        for port, positive, negative, sense in (("input", "p1", "n1", "in"), ("output", "p2", "n2", "out")):
            voltage = vector(nets[(cid, positive)]) - vector(nets[(cid, negative)])
            current = vector(f"i(v_coax_{cid}_{sense})")
            powers.append(.5 * (voltage * current.conjugate()).real if complex_mode else voltage * current)
            for letter, unit, values in (("V", "V", voltage), ("I", "A", current)):
                name = f"{letter}({cid}:{port})"
                if complex_mode:
                    result["signals"] += [{"name": name + " magnitude", "unit": unit, "values": np.abs(values[sample_indices]).tolist()},
                                          {"name": name + " phase", "unit": "deg", "values": np.angle(values[sample_indices], deg=True).tolist()}]
                elif kind == "op":
                    result["summary"][name] = float(values[0])
                else:
                    result["signals"].append({"name": name, "unit": unit, "values": samples(values)})
        power = powers[0] + powers[1]
        if periodic_time is not None:
            t = np.asarray(periodic_time)
            periodic_power = np.interp(t, x, power)
            cables[cid]["periodic_net_input_power_w"] = float(trapezoid(periodic_power, t) / (t[-1] - t[0]))
            result["signals"].append({"name": f"P({cid}) net input", "unit": "W", "values": periodic_power.tolist()})
        elif kind == "op":
            result["summary"][f"P({cid}) net input"] = float(power[0])
        else:
            result["signals"].append({"name": f"P({cid}) net input", "unit": "W", "values": power[sample_indices].tolist()})
    if cables:
        result.setdefault("model_metadata", {})["coax_cables"] = cables

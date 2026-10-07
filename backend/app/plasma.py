"""CCP matrix-sheath EDD and cycle-averaged 0-D plasma coupling.

The built-in template is deliberately separate from arbitrary schematics. RF
waveforms are always solved by the real PySpice/ngspice engine; scipy only solves
the outer DC-current and particle/electron-energy balance equations.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable

import numpy as np
from scipy.optimize import brentq, least_squares
from scipy.integrate import trapezoid

from .plasma_models import (
    ARGON_MODEL_VERSION, ARGON_REFERENCE, ATOMIC_MASS, BOLTZMANN,
    ELEMENTARY_CHARGE as E, ELECTRON_MASS as ME, EPSILON_0 as EPS,
    ReactionNetwork, argon_rates,
)


@dataclass(frozen=True)
class CCPSettings:
    gas: str = "Ar"
    frequency_hz: float = 40e6
    rf_peak_voltage: float = 250.0
    pressure_pa: float = 1.333223684
    gap_m: float = 0.05
    gas_temperature_k: float = 300.0
    cathode_diameter_m: float = 0.3
    area_ratio: float = 5.0
    electron_density_m3: float = 1e16
    electron_temperature_ev: float = 3.0
    momentum_collision_frequency_hz: float = 1e7
    ion_mass_amu: float = 39.948
    electronegativity: float = 0.0
    wall_edge_factor: float = 0.5
    plasma_volume_m3: float | None = None
    wall_loss_area_m2: float | None = None
    cycles: int = 80
    points_per_cycle: int = 256
    max_global_iterations: int = 18

    @classmethod
    def parse(cls, settings: dict[str, Any]) -> "CCPSettings":
        gas = str(settings.get("gas", "Ar"))
        if gas not in {"Ar", "O2", "CF4"}:
            raise ValueError("単独ガスAr・O₂・CF₄を指定してください")
        known = cls.__dataclass_fields__
        fields = {key: value for key, value in settings.items() if key in known}
        fields["gas"] = gas
        if "ion_mass_amu" not in fields:
            fields["ion_mass_amu"] = {"Ar": 39.948, "O2": 31.998, "CF4": 69.006}[gas]
        for key in fields:
            if key == "gas" or fields[key] is None:
                continue
            number = float(fields[key])
            if key in {"cycles", "points_per_cycle", "max_global_iterations"}:
                if isinstance(fields[key], bool) or not math.isfinite(number) or not number.is_integer():
                    raise ValueError(f"{key} は整数で指定してください")
                fields[key] = int(number)
            else:
                fields[key] = number
        result = cls(**fields)
        positive = ("frequency_hz", "rf_peak_voltage", "pressure_pa", "gap_m", "gas_temperature_k",
                    "cathode_diameter_m", "area_ratio", "electron_density_m3", "electron_temperature_ev",
                    "momentum_collision_frequency_hz", "ion_mass_amu", "wall_edge_factor")
        for name in positive:
            value = getattr(result, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} は正の有限値で指定してください")
        if not 1 <= result.electron_temperature_ev <= 20:
            raise ValueError("CCPの電子温度は1〜20 eVで指定してください")
        if not 1e10 <= result.electron_density_m3 <= 1e20:
            raise ValueError("電子密度は1e10〜1e20 m⁻³で指定してください")
        if result.wall_edge_factor > 1 or not 0 <= result.electronegativity <= 1000:
            raise ValueError("壁面密度比は0〜1、負イオン／電子密度比は0〜1000で指定してください")
        if not 16 <= result.cycles <= 120 or not 64 <= result.points_per_cycle <= 512:
            raise ValueError("RF周期数は16〜120、周期あたり点数は64〜512で指定してください")
        if not 3 <= result.max_global_iterations <= 40:
            raise ValueError("グローバルモデル反復上限は3〜40で指定してください")
        for name in ("plasma_volume_m3", "wall_loss_area_m2"):
            value = getattr(result, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} は正の有限値で指定してください")
        return result

    @property
    def cathode_area(self) -> float:
        return math.pi * (self.cathode_diameter_m / 2)**2

    @property
    def anode_area(self) -> float:
        return self.cathode_area * self.area_ratio

    @property
    def volume(self) -> float:
        return self.plasma_volume_m3 or self.cathode_area * self.gap_m

    @property
    def loss_area(self) -> float:
        return self.wall_loss_area_m2 or self.cathode_area + self.anode_area

    @property
    def neutral_density(self) -> float:
        return self.pressure_pa / (BOLTZMANN * self.gas_temperature_k)

    @property
    def ion_density(self) -> float:
        return self.electron_density_m3 * (1 + self.electronegativity)

    @property
    def bohm_velocity(self) -> float:
        return math.sqrt(E * self.electron_temperature_ev / (self.ion_mass_amu * ATOMIC_MASS))

    @property
    def inductance(self) -> float:
        return ME * self.gap_m / (E**2 * self.electron_density_m3 * self.cathode_area)

    @property
    def resistance(self) -> float:
        return self.inductance * self.momentum_collision_frequency_hz


def sheath_charge(voltage: Any, area: float, ion_density: float, temperature_ev: float = 3.0, smoothing_ev: float = 0.05) -> Any:
    """Metal-side charge for Vs = V(metal)-V(plasma), in C.

    Uniform cold ions give Q ~ -A sqrt(2 eps e ni (-Vs)) at large voltage.
    A thermal-voltage offset and Debye-capacitance continuation regularize
    collapse. Constant offsets have no effect on current or RF energy.
    """
    v = np.asarray(voltage, dtype=float)
    positive = (np.sqrt(v*v + smoothing_ev**2) - v) / 2
    plus = (np.sqrt(v*v + smoothing_ev**2) + v) / 2
    zero = smoothing_ev / 2
    prefactor = area * math.sqrt(2 * EPS * E * ion_density)
    debye_capacitance = area * math.sqrt(E * EPS * ion_density/temperature_ev)
    return -prefactor*(np.sqrt(positive+temperature_ev)-math.sqrt(zero+temperature_ev)) + debye_capacitance*(plus-zero)


def sheath_conduction(voltage: Any, area: float, settings: CCPSettings) -> Any:
    electron_flux = settings.electron_density_m3 * settings.wall_edge_factor * math.sqrt(
        E * settings.electron_temperature_ev / (2 * math.pi * ME))
    ion_flux = settings.ion_density * settings.wall_edge_factor * settings.bohm_velocity
    # Numerically cap large forward-bias trial currents smoothly. At a
    # repelling sheath the relative change is below exp(Vs/Te)/30. A positive
    # electron sheath is outside the physics scope and is diagnosed below.
    boltzmann = np.exp(np.minimum(np.asarray(voltage)/settings.electron_temperature_ev, 20))
    return E * area * (electron_flux*boltzmann/(1+boltzmann/30) - ion_flux)


def build_ccp_netlist(settings: CCPSettings, dc_bias_v: float) -> str:
    s = settings
    floating_potential = s.electron_temperature_ev * math.log(
        math.sqrt(E*s.electron_temperature_ev/(2*math.pi*ME)) / ((1+s.electronegativity)*s.bohm_velocity))
    node_seed = max(dc_bias_v, 0) + floating_potential
    ramp = f"min(time*{s.frequency_hz:.14g}/8,1)"
    lines = ["* CCP nonlinear matrix-sheath EDD benchmark",
             ".options reltol=1e-5 abstol=1e-10 vntol=1e-9 method=gear",
             f".nodeset V(pc)={node_seed:.14g} V(pa)={node_seed:.14g} V(bulk)={node_seed:.14g}",
             f"Vdrive cathode rfnode DC {dc_bias_v:.14g}",
             f"Bwave rfnode 0 V={s.rf_peak_voltage:.14g}*sin(2*pi*{s.frequency_hz:.14g}*time)*({ramp})^2*(3-2*({ramp}))",
             f"Rbulk pc bulk {s.resistance:.14g}",
             f"Lbulk bulk pa {s.inductance:.14g}"]
    for tag, metal, plasma, area in [("c", "cathode", "pc", s.cathode_area), ("a", "0", "pa", s.anode_area)]:
        v = f"v({metal},{plasma})"
        electron_current = E * area * s.electron_density_m3 * s.wall_edge_factor * math.sqrt(E*s.electron_temperature_ev/(2*math.pi*ME))
        ion_current = E * area * s.ion_density * s.wall_edge_factor * s.bohm_velocity
        q_prefactor = -area * math.sqrt(2*EPS*E*s.ion_density*s.wall_edge_factor)
        debye_c = area * math.sqrt(E*EPS*s.ion_density*s.wall_edge_factor/s.electron_temperature_ev)
        q = (f"({q_prefactor:.14g})*(sqrt((sqrt(({v})^2+0.0025)-({v}))/2+{s.electron_temperature_ev:.14g})-sqrt(0.025+{s.electron_temperature_ev:.14g}))"
             f"+({debye_c:.14g})*((sqrt(({v})^2+0.0025)+({v}))/2-0.025)")
        boltzmann = f"exp(min(({v})/{s.electron_temperature_ev:.14g},20))"
        lines += [f"Bcond_{tag} {metal} {plasma} I={electron_current:.14g}*({boltzmann})/(1+({boltzmann})/30)-{ion_current:.14g}",
                  f"Bcharge_{tag} q{tag} 0 V=({q})/1e-8",
                  f"Ccharge_{tag} q{tag} 0 1e-8",
                  f"Fcharge_{tag} {metal} {plasma} Bcharge_{tag} -1"]
    lines += [".end"]
    return "\n".join(lines) + "\n"


def _vector(result: dict[str, Any], name: str) -> np.ndarray:
    values = result.get("vectors", {})
    alternatives = (name, name.lower(), f"v({name.lower()})", f"V({name})")
    for key in alternatives:
        if key in values:
            return np.asarray(values[key], dtype=float)
    for signal in result.get("signals", []):
        if str(signal["name"]).lower() in {k.lower() for k in alternatives}:
            return np.asarray(signal["values"], dtype=float)
    raise RuntimeError(f"ngspice出力に必要なベクトル {name} がありません")


def _cycle_samples(result: dict[str, Any], settings: CCPSettings, cycles: int = 2) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    x = np.asarray(result.get("x", result["axis"]["values"]), dtype=float)
    end = x[-1]
    start = end - cycles / settings.frequency_hz
    # Integrate on ngspice's adaptive time mesh. Uniform resampling before
    # averaging would miss narrow sheath-electron pulses and make the outer
    # zero-DC-current root noisy.
    target = np.concatenate(([start], x[(x > start) & (x < end)], [end]))
    vectors = {name: np.interp(target, x, _vector(result, name)) for name in ("cathode", "pc", "bulk", "pa", "i(vdrive)")}
    return target, vectors


def _mean(t: np.ndarray, value: np.ndarray) -> float:
    return float(trapezoid(value, t) / (t[-1] - t[0]))


def _ccp_metadata(settings: CCPSettings) -> dict[str, Any]:
    return {
        "template": "builtin_ccp", "name": "Uniform-ion matrix sheath / Drude bulk CCP",
        "version": "ccp-matrix-sheath-0.1", "status": "reduced_model_unvalidated",
        "source": {"title": "Principles of Plasma Discharges and Materials Processing, 2nd edition",
                   "url": "https://doi.org/10.1002/0471724254"},
        "input_settings": asdict(settings),
        "assumptions": [
            "固定波形の駆動電極電圧 Vdc+Vpk sin(ωt)。周期平均電極電流0からVdcを求める。",
            "冷たい一様イオンのmatrix sheath。崩壊付近は熱電圧offsetとDebye容量で正則化。",
            "電子反発側でBohmイオン流束とMaxwell電子流束を使用。",
            "探索時の順方向電子電流は熱流束の30倍で滑らかに制限。正の電子シースは適用範囲外。",
            "バルクはDrude R+L。電子加熱電力は周期平均のRbulk I²のみ。",
            "momentum_collision_frequency_hzは入力仮定であり、ガスの検証済み衝突データではない。",
            "空間平均モデル。シース加熱・二次電子放出・非Maxwell分布・電磁伝播を含まない。",
            "O₂/CF₄固定条件では単一有効イオン質量を使用。負イオンのBohm条件補正は未実装。",
        ],
        "geometry": {"cathode_area_m2": settings.cathode_area, "anode_area_m2": settings.anode_area,
                     "plasma_volume_m3": settings.volume, "wall_loss_area_m2": settings.loss_area},
        "electron_heating_definition": "mean(Rbulk * Ibulk**2); sheath charge and ion acceleration excluded",
        "dc_boundary_condition": "no DC feed: mean electrode terminal current = 0",
        "startup": "RF amplitude smoothstep over 8 periods; exact input amplitude thereafter; final two cycles retained",
        "arbitrary_schematic_coupling": False,
    }


def solve_ccp(settings: CCPSettings, simulator: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    if simulator is None:
        from .engine import simulate_netlist
        simulator = simulate_netlist
    cache: dict[float, tuple[float, dict[str, Any]]] = {}
    period = 1 / settings.frequency_hz
    analysis = {"kind": "transient", "settings": {"time_step": period/settings.points_per_cycle,
                "max_step": period/settings.points_per_cycle, "stop_time": settings.cycles*period,
                "retain_all_samples": True}}

    def dc_current(bias: float) -> float:
        if bias not in cache:
            result = simulator(build_ccp_netlist(settings, bias), analysis)
            t, vectors = _cycle_samples(result, settings)
            cache[bias] = (_mean(t, -vectors["i(vdrive)"]), result)
        return cache[bias][0]

    # Matrix-sheath area scaling supplies a bracket only, never the reported
    # answer. Avoid strongly forward-biased, unphysical trial sheaths.
    estimate = -settings.rf_peak_voltage * (settings.area_ratio**2-1)/(settings.area_ratio**2+1)
    if settings.area_ratio != 1:
        estimate += math.copysign(4*settings.electron_temperature_ev, settings.area_ratio-1)
    width = settings.rf_peak_voltage*.1 + 2*settings.electron_temperature_ev
    low_bias, high_bias = estimate-width, estimate+width
    left, right = dc_current(low_bias), dc_current(high_bias)
    if left * right >= 0:
        for multiplier in (2, 3, 5):
            low_bias, high_bias = estimate-width*multiplier, estimate+width*multiplier
            left, right = dc_current(low_bias), dc_current(high_bias)
            if left*right < 0:
                break
        else:
            raise RuntimeError("DC自己バイアスの探索範囲で平均電流の符号が変わりません。モデル・周期数・密度を確認してください")
    bias = float(brentq(dc_current, low_bias, high_bias, xtol=1e-4, rtol=1e-8, maxiter=28))
    dc_current(bias)
    raw = cache[bias][1]
    t, v = _cycle_samples(raw, settings)
    current = (v["pc"] - v["bulk"]) / settings.resistance
    vc, va = v["cathode"] - v["pc"], -v["pa"]
    ni = settings.ion_density * settings.wall_edge_factor
    qc, qa = sheath_charge(vc, settings.cathode_area, ni, settings.electron_temperature_ev), sheath_charge(va, settings.anode_area, ni, settings.electron_temperature_ev)
    ic = sheath_conduction(vc, settings.cathode_area, settings)
    ia = sheath_conduction(va, settings.anode_area, settings)
    electron_power = _mean(t, current**2*settings.resistance)
    port_power = _mean(t, v["cathode"] * -v["i(vdrive)"])
    sheath_power = _mean(t, vc*ic + va*ia)
    ion_flux_current = E * settings.ion_density * settings.wall_edge_factor * settings.bohm_velocity
    ion_power = _mean(t, np.maximum(-vc, 0)*ion_flux_current*settings.cathode_area
                      + np.maximum(-va, 0)*ion_flux_current*settings.anode_area)
    sample_n = settings.points_per_cycle
    cycle_rms = math.sqrt(_mean(t, current**2))
    periodic_grid = np.linspace(t[0], t[-1], 2*sample_n+1)
    periodic_current = np.interp(periodic_grid, t, current)
    periodic_error = float(np.sqrt(np.mean((periodic_current[:sample_n] - periodic_current[sample_n:2*sample_n])**2)) / max(cycle_rms, 1e-12))
    def periodic_error_of(values: np.ndarray) -> float:
        sampled = np.interp(periodic_grid, t, values)
        difference = sampled[:sample_n] - sampled[sample_n:2*sample_n]
        return float(np.sqrt(np.mean(difference**2))/max(math.sqrt(_mean(t, values**2)), 1e-20))
    voltage_periodic_error = max(periodic_error_of(vc), periodic_error_of(va))
    charge_periodic_error = max(periodic_error_of(qc), periodic_error_of(qa))
    power_residual = port_power-electron_power-sheath_power
    power_relative_error = abs(power_residual)/max(abs(port_power), electron_power, abs(sheath_power), 1e-20)
    average_current = _mean(t, -v["i(vdrive)"])
    ion_cathode_current = E * settings.ion_density * settings.wall_edge_factor * settings.bohm_velocity * settings.cathode_area
    current_tolerance = max(1e-6, ion_cathode_current * .01)
    converged = (abs(average_current) < current_tolerance and periodic_error < .02
                 and voltage_periodic_error < .02 and charge_periodic_error < .02
                 and power_relative_error < .01)
    signals: list[dict[str, Any]] = []
    for name, unit, values in [
        ("V(cathode)", "V", v["cathode"]), ("V(plasma_cathode)", "V", v["pc"]),
        ("V(plasma_anode)", "V", v["pa"]), ("V(sheath_cathode)", "V", -vc),
        ("V(sheath_anode)", "V", -va),
        ("V(metal_minus_plasma_cathode)", "V", vc),
        ("V(metal_minus_plasma_anode)", "V", va),
        ("I(electrode)", "A", -v["i(vdrive)"]),
        ("I(bulk)", "A", current), ("Q(sheath_cathode)", "C", qc),
        ("Q(sheath_anode)", "C", qa), ("P(electron_bulk)", "W", current**2*settings.resistance),
    ]:
        signals.append({"name": name, "unit": unit, "values": values.tolist()})
    diagnostics = {"dc_root_evaluations": len(cache), "mean_electrode_current_a": average_current,
                   "dc_current_tolerance_a": current_tolerance, "periodic_current_relative_error": periodic_error,
                   "periodic_sheath_voltage_relative_error": voltage_periodic_error,
                   "periodic_sheath_charge_relative_error": charge_periodic_error,
                   "port_minus_bulk_and_conductive_sheath_w": power_residual,
                   "rf_power_balance_relative_error": power_relative_error,
                   "convergence_limits": {"periodic_relative_error": .02, "rf_power_balance_relative_error": .01},
                   "sheath_collapse_fraction": _mean(t, ((vc > 0.2) | (va > 0.2)).astype(float)),
                   "retained_cycles": 2, "simulated_cycles": settings.cycles}
    logs = ["実際のPySpice/ngspice過渡解析でCCP EDD回路を計算。自己バイアスは平均電流0の外側反復。",
            "νmは入力仮定。電子加熱はバルク抵抗損失のみで、定量的なCCP検証は未実施。"]
    if not converged:
        logs.append("周期定常性・DC電流収支・RF電力収支のいずれかが基準を満たしていません。周期数や周期あたり点数を増やして再計算してください。")
    if settings.gas != "Ar":
        logs.append("分子ガス固定条件は入力した有効イオン・衝突頻度による縮約等価回路です。化学反応の予測ではありません。")
    return {"kind": "ccp", "converged": converged,
            "summary": {"dc_self_bias_v": bias, "electron_density_m3": settings.electron_density_m3,
                        "electron_temperature_ev": settings.electron_temperature_ev,
                        "electron_heating_w": electron_power, "electrode_absorbed_power_w": port_power,
                        "ion_acceleration_power_w": ion_power, "conductive_sheath_power_w": sheath_power,
                        "rf_current_rms_a": cycle_rms, "bulk_resistance_ohm": settings.resistance,
                        "bulk_inductance_h": settings.inductance, "neutral_density_m3": settings.neutral_density},
            "axis": {"name": "time", "unit": "s", "values": (t-t[0]).tolist()},
            "signals": signals, "tables": [], "logs": logs,
            "netlist": build_ccp_netlist(settings, bias), "solver": raw.get("solver", {}),
            "model_metadata": _ccp_metadata(settings), "diagnostics": diagnostics}


def argon_balances(settings: CCPSettings, electron_heating_w: float) -> dict[str, float]:
    """Ground-state particle and electron-energy residuals (m^-3 s^-1,W)."""
    ne, te = settings.electron_density_m3, settings.electron_temperature_ev
    rates = argon_rates(te)
    loss_rate = settings.wall_edge_factor * settings.bohm_velocity * settings.loss_area/settings.volume
    source = ne * settings.neutral_density * rates["ionization"]
    wall = ne * loss_rate
    inelastic_w = ne * settings.neutral_density * settings.volume * E * (
        rates["ionization"] * 15.76 + rates["excitation"] * 11.55)
    thermal_gas_ev = BOLTZMANN*settings.gas_temperature_k/E
    elastic_w = ne*settings.volume * 3*ME/(settings.ion_mass_amu*ATOMIC_MASS) * settings.momentum_collision_frequency_hz * E * max(te-thermal_gas_ev, 0)
    wall_w = wall * settings.volume * E * 2.5 * te
    loss_w = inelastic_w + elastic_w + wall_w
    return {"particle_source_m3_s": source, "particle_wall_loss_m3_s": wall,
            "particle_residual_m3_s": source-wall, "inelastic_loss_w": inelastic_w,
            "elastic_loss_w": elastic_w, "electron_and_bohm_wall_loss_w": wall_w,
            "electron_energy_loss_w": loss_w, "electron_heating_w": electron_heating_w,
            "energy_residual_w": electron_heating_w-loss_w}


def _solve_argon_global(settings: CCPSettings) -> dict[str, Any]:
    # The reduced Ar particle balance has a single temperature root. Unlike
    # assigning a target density/temperature, this follows ionization=Bohm loss.
    def particle_at(te: float) -> float:
        s = replace(settings, electron_temperature_ev=te)
        return s.neutral_density*argon_rates(te)["ionization"] - s.wall_edge_factor*s.bohm_velocity*s.loss_area/s.volume
    if particle_at(1.0) * particle_at(7.0) >= 0:
        raise RuntimeError("純Ar縮約モデルの粒子収支根が適用範囲1〜7 eVにありません。圧力・損失面積・壁面密度比を確認してください")
    te = float(brentq(particle_at, 1, 7))
    history: list[dict[str, float]] = []
    cache: dict[float, dict[str, Any]] = {}

    def residual(log_ne: float) -> float:
        ne = float(math.exp(log_ne))
        s = replace(settings, electron_density_m3=ne, electron_temperature_ev=te, electronegativity=0)
        rf = solve_ccp(s)
        b = argon_balances(s, rf["summary"]["electron_heating_w"])
        scale = max(b["electron_heating_w"], b["electron_energy_loss_w"], 1e-20)
        error = b["energy_residual_w"] / scale
        cache[log_ne] = rf
        history.append({"iteration": float(len(history)+1), "electron_density_m3": ne,
                        "electron_temperature_ev": te, "electron_heating_w": b["electron_heating_w"],
                        "electron_energy_loss_w": b["electron_energy_loss_w"], "relative_energy_residual": error})
        return error

    center = math.log(settings.electron_density_m3)
    pairs: list[tuple[float, float]] = []
    bracket: tuple[float, float] | None = None
    for offset in (0, -math.log(10), math.log(10), -math.log(100), math.log(100), -math.log(1000), math.log(1000)):
        x = min(max(center+offset, math.log(1e12)), math.log(1e19))
        if any(abs(x-p[0]) < 1e-9 for p in pairs):
            continue
        y = residual(x)
        pairs.append((x, y))
        ordered = sorted(pairs)
        for (a, fa), (b, fb) in zip(ordered, ordered[1:]):
            if fa*fb <= 0:
                bracket = (a, b)
                break
        if bracket:
            break
    if bracket is None:
        # Report model failure rather than fabricate a plasma state.
        raise RuntimeError("Arグローバル収支とRFバルク加熱が釣り合う密度を探索範囲で得られません。シース加熱を含まない縮約モデルの限界、入力νm、壁損失条件を確認してください")
    root = float(brentq(residual, *bracket, xtol=2e-3, maxiter=settings.max_global_iterations))
    if root not in cache:
        residual(root)
    final = cache[root]
    s = replace(settings, electron_density_m3=math.exp(root), electron_temperature_ev=te)
    balance = argon_balances(s, final["summary"]["electron_heating_w"])
    final["kind"] = "global"
    final["converged"] = final["converged"] and abs(balance["energy_residual_w"])/max(balance["electron_energy_loss_w"], 1e-20) < 0.01
    final["summary"].update({"electron_energy_density_j_m3": 1.5*s.electron_density_m3*E*te,
                              "Ar+_density_m3": s.electron_density_m3, "Ar_density_m3": s.neutral_density})
    final["diagnostics"]["global_balances"] = balance
    final["diagnostics"]["global_iterations"] = history
    final["tables"] = [{"name": "連成反復", "columns": list(history[0]), "rows": [list(row.values()) for row in history]}]
    final["model_metadata"]["chemistry"] = {
        "name": "Ground-state Ar particle/electron energy balance", "version": ARGON_MODEL_VERSION,
        "status": "experimental_reduced_model_unvalidated", "source": ARGON_REFERENCE,
        "temperature_range_ev": [1, 7], "electron_distribution": "Maxwellian",
        "species": ["Ar", "Ar+", "e"],
        "reactions": [
            {"equation": "e + Ar -> Ar+ + 2e", "rate_m3_s": "2.34e-14 * Te**0.59 * exp(-17.44/Te)", "energy_loss_ev": 15.76},
            {"equation": "e + Ar -> Ar* + e (lumped energy loss, Ar* not evolved)",
             "rate_m3_s": "2.48e-14 * Te**0.33 * exp(-12.78/Te)", "energy_loss_ev": 11.55}],
        "wall_energy_per_pair_ev": "2.5*Te: 2*Te electrons + 0.5*Te Bohm ion energy",
        "limitations": ["No metastable populations / stepwise ionization / neutral depletion",
                        "Ohmic-only electron heating; cannot predict a sheath-heated CCP quantitatively",
                        "Gas temperature and momentum collision frequency are fixed inputs",
                        "Only cycle-periodic steady state, not ignition or macroscopic transient"]}
    final["logs"].append("純Arの粒子収支からTe、RF周期平均バルク加熱と電子エネルギー収支からneを求めました。反応・連成モデルは実験未検証です。")
    return final


def _solve_user_global(settings: CCPSettings, data: dict[str, Any], initial: dict[str, float]) -> dict[str, Any]:
    if str(data.get("energy_mode", "")).startswith("particle_only"):
        raise ValueError("粒子専用の反応表には電子エネルギー収支がありません。グローバル解析には出典付きのエネルギー閉包が必要です")
    network = ReactionNetwork(data, settings.gas)
    if not network.dynamic:
        raise ValueError("グローバルモデルには時間発展する粒子種が必要です")
    if any(not math.isfinite(float(initial.get(n, 0))) or float(initial.get(n, 0)) <= 0 for n in network.dynamic):
        raise ValueError("initial_species_densities_m3 に全ての動的粒子種の正の初期密度を指定してください")
    initial_ne = network.quasineutral_electron_density(initial)
    if not 1e10 < initial_ne <= 1e20:
        raise ValueError("初期粒子密度の準中性条件から求める電子密度を1e10〜1e20 m⁻³の正値にしてください")
    history: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    ng = settings.neutral_density
    scale_density = settings.electron_density_m3
    minimum_te, maximum_te = max(1.0, network.min_temperature), min(20.0, network.max_temperature)
    if minimum_te >= maximum_te:
        raise ValueError("CCPモデルと反応係数表の温度範囲が重なりません")

    def residual(log_state: np.ndarray, final_evaluation: bool = False) -> np.ndarray:
        densities = {n: float(math.exp(v)) for n, v in zip(network.dynamic, log_state[:-1])}
        densities[settings.gas] = ng
        ne = network.quasineutral_electron_density(densities)
        if ne <= 1e10 or ne > 1e20:
            # Avoid pretending that a negative quasineutral density is a valid
            # plasma state. Least-squares may propose such trial points.
            if final_evaluation:
                raise RuntimeError("グローバル探索の最終粒子密度が準中性条件・密度範囲を満たしません")
            return np.full(len(log_state), 1e3 + abs(ne)/scale_density)
        te = float(math.exp(log_state[-1]))
        negative = sum(-s.charge*densities.get(s.name, 0) for s in network.species.values() if s.charge < 0)
        positive = [s for s in network.species.values() if s.charge > 0]
        if not positive:
            raise ValueError("グローバルモデルには正イオンが必要です")
        total_positive = sum(s.charge*densities[s.name] for s in positive)
        effective_mass = sum(s.mass_amu*s.charge*densities[s.name] for s in positive)/total_positive
        s = replace(settings, electron_density_m3=ne, electron_temperature_ev=te,
                    electronegativity=negative/ne, ion_mass_amu=effective_mass)
        if s.electronegativity > 1000:
            if final_evaluation:
                raise RuntimeError("グローバル探索の最終状態で負イオン／電子密度比が適用範囲を超えました")
            return np.full(len(log_state), 1e3 + s.electronegativity)
        wall_rates = {spec.name: (settings.wall_edge_factor*math.sqrt(E*te/(spec.mass_amu*ATOMIC_MASS))*settings.loss_area/settings.volume
                                  if spec.wall_loss_model == "bohm" else spec.wall_loss_s)
                      for spec in network.species.values()}
        terms, inelastic = network.source_terms(densities, te, wall_rates)
        # Quasineutral electron loss equals net charged-species loss to walls.
        electron_wall_rate = -network.wall_source_terms(densities, wall_rates)["e"]
        if electron_wall_rate < 0:
            if final_evaluation:
                raise RuntimeError("負イオンの壁損失が正イオン流束を超え、電子注入が必要な壁面収支です。このモデルでは未対応です")
            return np.full(len(log_state), 1e3 + abs(electron_wall_rate)/max(ne, 1))
        rf = solve_ccp(s)
        wall_power = 2*E*te*electron_wall_rate*settings.volume
        elastic_power = ne*settings.volume*3*ME/(effective_mass*ATOMIC_MASS)*s.momentum_collision_frequency_hz*E*max(te-BOLTZMANN*s.gas_temperature_k/E, 0)
        heating = rf["summary"]["electron_heating_w"]
        loss = inelastic*settings.volume + wall_power + elastic_power
        scales = [max(densities[n]*max(wall_rates[n], 1), 1) for n in network.dynamic]
        errors = [terms[n]/sc for n, sc in zip(network.dynamic, scales)] + [(heating-loss)/max(heating, loss, 1e-20)]
        history.append({"iteration": len(history)+1, "electron_density_m3": ne, "electron_temperature_ev": te,
                        "electron_heating_w": heating, "electron_energy_loss_w": loss,
                        "max_scaled_residual": max(abs(e) for e in errors)})
        last.update({"rf": rf, "densities": densities, "terms": terms, "heating": heating,
                     "loss": loss, "settings": s, "errors": errors, "wall_rates": wall_rates,
                     "electron_wall_rate_m3_s": electron_wall_rate})
        return np.asarray(errors)

    x0 = np.log([float(initial[n]) for n in network.dynamic] + [min(max(settings.electron_temperature_ev, minimum_te*1.001), maximum_te*.999)])
    lower = np.log([1e6]*len(network.dynamic) + [minimum_te])
    upper = np.log([1e23]*len(network.dynamic) + [maximum_te])
    solution = least_squares(residual, x0, bounds=(lower, upper), max_nfev=settings.max_global_iterations,
                             xtol=2e-3, ftol=2e-3, gtol=2e-3, diff_step=5e-3)
    errors = residual(solution.x, final_evaluation=True)
    result = last["rf"]
    result["kind"] = "global"
    result["converged"] = bool(result["converged"] and max(abs(errors)) < .02)
    result["summary"].update({f"{name}_density_m3": value for name, value in last["densities"].items()})
    result["diagnostics"]["global_balances"] = {"species_residuals_m3_s": last["terms"],
            "electron_heating_w": last["heating"], "electron_energy_loss_w": last["loss"],
            "wall_rates_s": last["wall_rates"], "electron_wall_loss_m3_s": last["electron_wall_rate_m3_s"],
            "scaled_residuals": errors.tolist(), "least_squares_status": int(solution.status)}
    result["diagnostics"]["global_iterations"] = history
    result["model_metadata"]["chemistry"] = network.metadata()
    result["model_metadata"]["chemistry"]["dataset"] = data
    result["logs"].append("ユーザー登録の反応表を用いた準中性粒子収支・電子エネルギー収支。反応データと壁損失は未検証です。")
    if not result["converged"]:
        result["logs"].append("グローバル収支は収束基準を満たしていません。この値を定常解として解釈しないでください。")
    return result


def execute_plasma(document: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    if document.get("parameters", {}).get("builtin_ccp_template") != 1:
        raise ValueError("CCP・グローバル解析は専用CCP設定テンプレートを選択してください。任意回路図との連成は未対応です")
    if document.get("components") or document.get("wires"):
        raise ValueError("CCP設定テンプレートに追加した回路部品・配線は連成できません。通常回路解析を使うか、専用テンプレートに戻してください")
    settings_data = analysis.get("settings", {})
    settings = CCPSettings.parse(settings_data)
    kind = analysis.get("kind")
    if kind == "ccp":
        return solve_ccp(settings)
    if kind != "global":
        raise ValueError("プラズマ解析はccpまたはglobalを指定してください")
    if settings_data.get("reaction_model"):
        return _solve_user_global(settings, settings_data["reaction_model"], settings_data.get("initial_species_densities_m3", {}))
    if settings.gas == "Ar":
        return _solve_argon_global(settings)
    raise ValueError("O₂・CF₄のグローバル解析には出典・負イオン・解離種を含むreaction_modelとinitial_species_densities_m3が必要です。固定密度・温度のCCP解析は実行できます。未検証の係数を自動生成しません")

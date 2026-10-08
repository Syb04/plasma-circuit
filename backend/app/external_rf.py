"""Physical external RF circuits and periodic drive definitions.

The external circuit contains the same matrix-sheath stamp as the legacy CCP
benchmark. All RF samples, capacitor charging and network losses come from
ngspice, including when a chemistry solver changes the plasma parameters.
"""
from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from typing import Any, Callable

import numpy as np
from scipy.optimize import brentq


def common_fundamental_frequency(frequencies: list[float], fundamental: float | None = None) -> float:
    """Validate a common periodic integration window for all circuit sources."""
    if not frequencies or any(not math.isfinite(f) or f <= 0 for f in frequencies):
        raise ValueError("RF周波数は正の有限値で指定してください")
    primary = frequencies[0]
    if fundamental is None:
        denominator = 1
        fractions = []
        for frequency in frequencies:
            ratio = frequency/primary
            rational = Fraction(ratio).limit_denominator(64)
            if not math.isclose(float(rational), ratio, rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError("RF・パルス周波数は共通周期を持つ整数比で指定してください。fundamental_frequency_hzも指定できます")
            fractions.append(rational)
            denominator = math.lcm(denominator, rational.denominator)
        numerator = math.gcd(*(r.numerator*(denominator//r.denominator) for r in fractions))
        fundamental = primary*numerator/denominator
    if not math.isfinite(fundamental) or fundamental <= 0:
        raise ValueError("fundamental_frequency_hz は正値で指定してください")
    for frequency in frequencies:
        ratio = frequency/fundamental
        if ratio < 1 or not math.isclose(ratio, round(ratio), rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("全てのRF・パルス周波数はfundamental_frequency_hzの整数倍にしてください")
    return fundamental


@dataclass(frozen=True)
class RFDrive:
    frequency_hz: float
    peak_voltage: float
    fundamental_frequency_hz: float
    second_frequency_hz: float | None = None
    second_peak_voltage: float = 0.0
    second_phase_deg: float = 0.0
    pulse_frequency_hz: float | None = None
    pulse_duty_cycle: float = 0.5
    pulse_off_fraction: float = 0.0

    @classmethod
    def from_settings(cls, data: dict[str, Any]) -> "RFDrive":
        def number(key: str, default: float | None = None) -> float | None:
            value = data.get(key, default)
            if value is None:
                return None
            if isinstance(value, bool):
                raise ValueError(f"{key} は有限の数値で指定してください")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{key} は有限の数値で指定してください")
            return value

        primary = number("frequency_hz", 40e6)
        peak = number("rf_peak_voltage", 250)
        second = number("second_frequency_hz")
        second_peak = number("second_rf_peak_voltage", 0)
        phase = number("second_phase_deg", 0)
        pulse = number("pulse_frequency_hz")
        duty = number("pulse_duty_cycle", .5)
        off = number("pulse_off_fraction", 0)
        fundamental = number("fundamental_frequency_hz")
        if primary <= 0 or peak <= 0 or second_peak < 0:
            raise ValueError("RF周波数・主振幅は正値、第二振幅は非負で指定してください")
        if second is not None and second <= 0 or pulse is not None and pulse <= 0:
            raise ValueError("第二周波数・パルス周波数は正値で指定してください")
        if second_peak and second is None:
            raise ValueError("第二RF振幅にはsecond_frequency_hzが必要です")
        if not 0 < duty <= 1 or not 0 <= off <= 1:
            raise ValueError("pulse_duty_cycleは0超〜1、pulse_off_fractionは0〜1で指定してください")
        frequencies = [primary]
        if second is not None and second_peak:
            frequencies.append(second)
        if pulse is not None and duty < 1 and off < 1:
            frequencies.append(pulse)
        fundamental = common_fundamental_frequency(frequencies, fundamental)
        return cls(primary, peak, fundamental, second, second_peak, phase, pulse, duty, off)

    def expression(self, ramp_periods: float = 8) -> str:
        """A numeric ngspice B-source expression; no author code is executed."""
        wave = f"{self.peak_voltage:.14g}*sin(2*pi*{self.frequency_hz:.14g}*time)"
        if self.second_frequency_hz is not None and self.second_peak_voltage:
            wave += f"+{self.second_peak_voltage:.14g}*sin(2*pi*{self.second_frequency_hz:.14g}*time+{math.radians(self.second_phase_deg):.14g})"
        envelope = "1"
        if self.pulse_frequency_hz is not None and self.pulse_duty_cycle < 1:
            phase = f"(time*{self.pulse_frequency_hz:.14g}-floor(time*{self.pulse_frequency_hz:.14g}))"
            envelope = f"(({phase})<{self.pulse_duty_cycle:.14g}?1:{self.pulse_off_fraction:.14g})"
        if ramp_periods:
            ramp = f"min(time*{self.fundamental_frequency_hz:.14g}/{ramp_periods:.14g},1)"
            envelope += f"*({ramp})^2*(3-2*({ramp}))"
        return f"({wave})*({envelope})"

    def metadata(self) -> dict:
        return asdict(self)


def template_document(settings: Any, external: dict[str, Any]) -> dict:
    """Create the explicitly selected source/matching/DC-block circuit."""
    from .presets import component, document, wire
    if not isinstance(external, dict):
        raise ValueError("external_circuit は設定オブジェクトで指定してください")
    allowed = {"source_resistance_ohm", "series_inductance_h", "shunt_capacitance_f", "dc_block_capacitance_f", "dc_voltage_v", "voltage_definition", "reference_impedance_ohm"}
    unknown = set(external)-allowed
    if unknown:
        raise ValueError("external_circuitの未対応設定: "+", ".join(sorted(unknown)))
    if external.get("voltage_definition", "source") != "source":
        raise ValueError("外部回路のrf_peak_voltageはsource電圧です。電極振幅は回路から計算します")
    nodes = [component("rf_source", "V", 80, 120, {"dc": external.get("dc_voltage_v", 0), "waveform": {"kind": "rf", **asdict(settings)}}), component("plasma", "PLASMA", 560, 120, {}), component("gnd", "GND", 80, 320, {})]
    wires = [wire("return_source", "rf_source", "n", "gnd", "g"), wire("return_plasma", "plasma", "n", "gnd", "g")]
    previous = ("rf_source", "p")
    for cid, kind, key in [("source_r", "R", "source_resistance_ohm"), ("match_l", "L", "series_inductance_h"), ("dc_block", "C", "dc_block_capacitance_f")]:
        value = external.get(key, 50 if key == "source_resistance_ohm" else 0)
        if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"{key}は非負の有限値で指定してください")
        if not float(value):
            continue
        nodes.append(component(cid, kind, 230+len(nodes)*25, 120, {"value": value}))
        wires.append(wire("series_"+cid, previous[0], previous[1], cid, "p"))
        previous = (cid, "n")
    wires.append(wire("electrode", previous[0], previous[1], "plasma", "p"))
    capacitance = external.get("shunt_capacitance_f", 0)
    if isinstance(capacitance, bool) or not math.isfinite(float(capacitance)) or float(capacitance) < 0:
        raise ValueError("shunt_capacitance_fは非負の有限値で指定してください")
    if float(capacitance):
        nodes.append(component("match_c", "C", 440, 270, {"value": capacitance}))
        wires.extend([wire("shunt_p", "match_c", "p", "plasma", "p"), wire("shunt_n", "match_c", "n", "gnd", "g")])
    return document("External RF CCP", "RF source, matching network and physical plasma load", nodes, wires)


def _dc_cluster(document: dict, start: str, nets: dict) -> set[str]:
    """Conservative DC connectivity excluding sheath/plasma and capacitors."""
    adjacency: dict[str, set[str]] = {}
    for component in document.get("components", []):
        kind, cid = component["kind"].upper(), component["id"]
        if kind in {"C", "PLASMA", "GND", "JUNCTION", "K"}:
            continue
        terminals = [net for (owner, _), net in nets.items() if owner == cid]
        if kind == "COAX_GND":
            # The implicit shield ground is separated by capacitors at DC.
            pairs = [(nets[(cid, "p1")], nets[(cid, "p2")])]
        elif kind in {"T", "O", "Y", "P", "COAX"} and len(terminals) == 4:
            pairs = [(terminals[0], terminals[2]), (terminals[1], terminals[3])]
        else:
            pairs = [(a, b) for a in terminals for b in terminals if a != b]
        for a, b in pairs:
            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)
    seen, pending = {start}, [start]
    while pending:
        for adjacent in adjacency.get(pending.pop(), set())-seen:
            seen.add(adjacent)
            pending.append(adjacent)
    return seen


def solve_external_ccp(settings: Any, document: dict, settings_data: dict | None = None,
                       simulator: Callable | None = None) -> dict:
    """Solve one PLASMA load with its actual source and external network."""
    from .engine import _element_name, _topology, build_netlist, simulate_netlist
    from .plasma import _ccp_metadata, _cycle_samples, _mean, _vector, summarize_ccp
    data = settings_data or {}
    simulator = simulator or simulate_netlist
    by_id, ports, nets = _topology(document)
    loads = [c for c in by_id.values() if c["kind"].upper() == "PLASMA"]
    if len(loads) != 1:
        raise ValueError("CCP連成にはPLASMA部品を1個だけ配置してください。複数プラズマの境界条件は未対応です")
    cid = loads[0]["id"]
    if ports[cid] != ["p", "n"]:
        raise ValueError("PLASMAは駆動p・帰還nの2端子で指定してください")
    p, n = nets[(cid, "p")], nets[(cid, "n")]
    if p == n:
        raise ValueError("PLASMAの駆動・帰還端子を短絡できません")
    sources = [c for c in by_id.values() if c["kind"].upper() == "V"]
    source_id = data.get("rf_source_id")
    if source_id:
        source = by_id.get(source_id)
        if source not in sources:
            raise ValueError("rf_source_idには電圧源の部品IDを指定してください")
    else:
        rf_sources = [c for c in sources if c.get("parameters", {}).get("waveform")]
        candidates = rf_sources or sources
        if len(candidates) != 1:
            raise ValueError("RF電圧源が複数ある場合はrf_source_idを指定してください")
        source = candidates[0]
    source_id = source["id"]
    source_p, source_n = nets[(source_id, "p")], nets[(source_id, "n")]
    source_port = data.get("rf_source_port")
    port_p, port_n = source_p, source_n
    if source_port:
        try:
            port_p = nets[(source_port["component_id"], source_port["port"])]
        except (KeyError, TypeError) as exc:
            raise ValueError("rf_source_portに有効なcomponent_id・portを指定してください") from exc
    elif "source_r" in by_id:
        port_p = nets[("source_r", "n")]
    port_resistor = None
    if port_p != source_p:
        resistors = []
        for candidate in by_id.values():
            if candidate["kind"].upper() != "R":
                continue
            rp, rn = (nets[(candidate["id"], port)] for port in ("p", "n"))
            if {rp, rn} == {source_p, port_p}:
                resistors.append(candidate)
        if len(resistors) != 1:
            raise ValueError("RF観測ポートは電圧源のp端子、またはその直列出力抵抗の負荷側端子で指定してください")
        port_resistor = resistors[0]
    # An external template source is controlled by the current RF settings;
    # schematic source waveforms remain the author's explicit drive.
    from .expressions import parse_si
    frequencies = []
    source_waveforms = {}
    for voltage_source in sources:
        waveform = voltage_source.get("parameters", {}).get("waveform")
        if not waveform:
            continue
        kind = str(waveform.get("kind", "")).lower()
        if kind == "sin":
            if parse_si(waveform.get("damping", 0)) != 0:
                raise ValueError("周期CCP解析では減衰sin電源を使用できません")
            frequencies.append(parse_si(waveform.get("frequency", 1e6)))
        elif kind == "rf":
            source_drive = RFDrive.from_settings(waveform)
            frequencies.append(source_drive.frequency_hz)
            if source_drive.second_peak_voltage:
                frequencies.append(source_drive.second_frequency_hz)
            if source_drive.pulse_frequency_hz and source_drive.pulse_duty_cycle < 1 and source_drive.pulse_off_fraction < 1:
                frequencies.append(source_drive.pulse_frequency_hz)
        else:
            raise ValueError("周期CCP解析の電圧源波形にはsinまたはrfを使用してください")
        source_waveforms[voltage_source["id"]] = waveform
    if not frequencies:
        raise ValueError("CCP連成にはsinまたはrf波形のRF電圧源が必要です")
    common_frequency = common_fundamental_frequency(frequencies, settings.fundamental_frequency_hz)
    settings = replace(settings, fundamental_frequency_hz=common_frequency)
    drive = RFDrive.from_settings(asdict(settings))
    fastest = max(frequencies)
    period = 1/drive.fundamental_frequency_hz
    analysis = {"kind": "transient", "settings": {"time_step": 1/fastest/settings.points_per_cycle,
                "max_step": 1/fastest/settings.points_per_cycle, "stop_time": settings.cycles*period,
                "retain_all_samples": True, "plasma_settings": asdict(settings)}}
    cluster = _dc_cluster(document, p, nets)
    isolated = n not in cluster and "0" not in cluster
    if isolated and not any(c["kind"].upper() == "C" and len({nets[(c["id"], port)] in cluster for port in ports[c["id"]]}) > 1 for c in by_id.values()):
        raise ValueError("DCから絶縁されたPLASMA駆動端子には電荷を保持するDCブロック容量が必要です")
    cache: dict[float, tuple[float, dict, str]] = {}

    def sample(bias: float) -> tuple[float, dict, str]:
        if bias in cache:
            return cache[bias]
        transient = {"kind": "transient", "settings": dict(analysis["settings"])}
        circuit = document
        if isolated:
            # Initial capacitor charge is the shooting variable. The capacitor
            # remains in the circuit and evolves under the real terminal current.
            seed = settings.electron_temperature_ev*math.log(math.sqrt(1.602176634e-19*settings.electron_temperature_ev/(2*math.pi*9.1093837139e-31))/((1+settings.electronegativity)*settings.bohm_velocity))
            initial = {node: bias for node in cluster}
            initial.update({f"pl_{cid}_pc": max(bias, 0)+seed, f"pl_{cid}_bulk": max(bias, 0)+seed, f"pl_{cid}_pa": max(bias, 0)+seed})
            if settings.sheath_heating_resistance_ohm:
                initial[f"pl_{cid}_bulk_heat"] = max(bias, 0)+seed
            transient["settings"].update(initial_conditions=initial, use_initial_condition=True)
            circuit = deepcopy(document)
            for line in circuit.get("components", []):
                if line["kind"].upper() != "T":
                    continue
                line_id = line["id"]
                def line_voltage(positive: str, negative: str) -> float:
                    return initial.get(nets[(line_id, positive)], 0)-initial.get(nets[(line_id, negative)], 0)
                line["parameters"] = {**line.get("parameters", {}), "initial": [line_voltage("p1", "n1"), 0, line_voltage("p2", "n2"), 0]}
        netlist = build_netlist(circuit, transient)
        raw = simulator(netlist, transient)
        x = np.asarray(raw.get("x", raw["axis"]["values"]), dtype=float)
        ground = np.zeros(len(x))
        def voltage(node: str) -> np.ndarray:
            return ground if node == "0" else _vector(raw, node)
        reference = voltage(n)
        normalized = {"cathode": voltage(p)-reference, "pc": voltage(f"pl_{cid}_pc")-reference,
                      "bulk": voltage(f"pl_{cid}_bulk")-reference, "pa": voltage(f"pl_{cid}_pa")-reference,
                      "i(vdrive)": -_vector(raw, f"i(v_pl_{cid}_sense)")}
        normalized_raw = {"x": x.tolist(), "axis": raw["axis"], "vectors": {key: value.tolist() for key, value in normalized.items()}}
        t, vectors = _cycle_samples(normalized_raw, settings)
        average = _mean(t, -vectors["i(vdrive)"])
        raw["_plasma_samples"] = (t, vectors)
        if port_resistor:
            from .expressions import parse_si
            source_current = (voltage(source_p)-voltage(port_p))/parse_si(port_resistor["parameters"]["value"])
        else:
            source_current = -_vector(raw, f"i({_element_name(source)})")
        raw["_source_samples"] = (np.interp(t, x, voltage(port_p)-voltage(port_n)), np.interp(t, x, source_current))
        cache[bias] = average, raw, netlist
        return cache[bias]

    if isolated:
        peak = drive.peak_voltage+drive.second_peak_voltage
        estimate = -peak*(settings.area_ratio**2-1)/(settings.area_ratio**2+1)
        width = peak*.2+4*settings.electron_temperature_ev
        for multiplier in (1, 2, 4, 8):
            lo, hi = estimate-width*multiplier, estimate+width*multiplier
            if sample(lo)[0]*sample(hi)[0] < 0:
                break
        else:
            raise RuntimeError("DCブロックの周期電荷平衡を探索範囲で得られません。容量・周期数・RF駆動を確認してください")
        initial_bias = float(brentq(lambda value: sample(value)[0], lo, hi, xtol=1e-4, maxiter=32))
    else:
        initial_bias = 0.0
    _, raw, netlist = sample(initial_bias)
    t, vectors = raw.pop("_plasma_samples")
    source_voltage, source_current = raw.pop("_source_samples")
    from .coax import add_results
    original_signal_count = len(raw["signals"])
    add_results(raw, document, nets, "transient", periodic_time=t)
    coax_signals = raw["signals"][original_signal_count:]
    bias = _mean(t, vectors["cathode"])
    metadata = _ccp_metadata(settings)
    metadata.update(template="external_circuit", arbitrary_schematic_coupling=True,
                    dc_boundary_condition="DC block periodic charge equilibrium" if isolated else "explicit DC feed: current follows circuit topology",
                    startup="RF sources of kind rf use an eight common-period smoothstep; schematic sources retain their explicit waveform")
    if "coax_cables" in raw["model_metadata"]:
        metadata["coax_cables"] = raw["model_metadata"]["coax_cables"]
    metadata["assumptions"][0] = "電極電圧・自己バイアス・端子電流は外部回路とPLASMAの同時ngspice解析から求める。"
    reference_impedance = float(data.get("source_reference_impedance_ohm", data.get("external_circuit", {}).get("reference_impedance_ohm", 50)))
    if not math.isfinite(reference_impedance) or reference_impedance <= 0:
        raise ValueError("source_reference_impedance_ohmは正の有限値で指定してください")
    metadata["rf_drive"] = {**drive.metadata(), "voltage_definition": "source", "rf_source_id": source_id,
                            "source_port_positive_node": port_p, "source_port_negative_node": port_n,
                            "source_port_current_definition": "positive from generator into external network",
                            "reference_impedance_ohm": reference_impedance, "source_waveforms": source_waveforms, "circuit_frequencies_hz": sorted(set(frequencies))}
    result = summarize_ccp(settings, raw, t, vectors, bias=bias, netlist=netlist,
                           dc_root_evaluations=len(cache) if isolated else 0, dc_equilibrium_required=isolated,
                           model_metadata=metadata, extra_signals=[{"name": "V(source_port)", "unit": "V", "values": source_voltage.tolist()},
                                                                 {"name": "I(source_port)", "unit": "A", "values": source_current.tolist()}] + coax_signals)
    result["diagnostics"].update(node_map={f"{owner}.{port}": node for (owner, port), node in nets.items()},
                                 plasma_component_id=cid, dc_block_initial_bias_v=initial_bias if isolated else None,
                                 source_port_absorbed_power_w=_mean(t, source_voltage*source_current),
                                 fundamental_frequency_hz=drive.fundamental_frequency_hz)
    result["logs"][0] = "実際のPySpice/ngspiceで外部回路とPLASMAを同時過渡解析。"+("DCブロック初期電荷を周期電荷平衡へ反復。" if isolated else "DC電流は明示した給電回路から計算。")
    return result

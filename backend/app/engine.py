"""Graph compilation and real PySpice/ngspice execution.

The mathematical compiler never executes Python supplied by a circuit author.
ngspice is kept behind the worker process boundary, including native models.
"""
from __future__ import annotations

import importlib.metadata
import ctypes.util
import math
import os
import re
from typing import Any

import numpy as np

from .catalog import BUILTIN_MODELS, COMPONENTS
from .expressions import ExpressionError, compile_expression, parse_si


class CircuitError(ValueError):
    pass


class SolverError(RuntimeError):
    pass


MAX_SAMPLES = 20_000
MAX_REQUESTED_POINTS = 250_000
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_NODE = re.compile(r"^[A-Za-z0-9_]+$")
_DEFAULTS = {entry["kind"]: entry for entry in COMPONENTS}


def _identifier(value: Any, what: str = "識別子") -> str:
    value = str(value)
    if not _IDENTIFIER.fullmatch(value) or len(value) > 100:
        raise CircuitError(f"{what}は英字から始まる英数字・アンダースコアで指定してください: {value}")
    return value


def _number(value: Any, positive: bool = False, nonnegative: bool = False) -> str:
    try:
        number = parse_si(value)
    except (ValueError, TypeError) as exc:
        raise CircuitError(f"数値が不正です: {value}") from exc
    if not math.isfinite(number) or (positive and number <= 0) or (nonnegative and number < 0):
        raise CircuitError(f"数値の範囲が不正です: {value}")
    return format(number, ".16g")


def _safe_native(value: Any) -> str:
    value = str(value).strip()
    if len(value) > 10000 or not re.fullmatch(r"[A-Za-z0-9_ .+*/=,{}()^?:!<>\-]*", value):
        raise CircuitError("詳細パラメータに改行・制御構文・ファイル参照は指定できません")
    return value


def _element_name(component: dict) -> str:
    cid = _identifier(component["id"], "部品 ID")
    kind = component["kind"].upper()
    if cid.upper().startswith(kind) and kind != "EDD":
        return cid
    return f"{kind}_{cid}"


def _topology(document: dict) -> tuple[dict, dict, dict]:
    components = document.get("components", [])
    if not components:
        raise CircuitError("回路に部品を配置してください")
    ids = set()
    by_id = {}
    parent = {}
    ports = {}
    for component in components:
        cid = _identifier(component["id"], "部品 ID")
        if cid in ids:
            raise CircuitError(f"部品 ID が重複しています: {cid}")
        ids.add(cid)
        by_id[cid] = component
        kind = component["kind"].upper()
        if kind not in _DEFAULTS:
            raise CircuitError(f"未対応の素子です: {kind}")
        listed = component.get("ports") or _DEFAULTS[kind]["ports"]
        if len(set(listed)) != len(listed):
            raise CircuitError(f"端子名が重複しています: {cid}")
        ports[cid] = listed
        for port in listed:
            _identifier(port, "端子名")
            parent[(cid, port)] = (cid, port)

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b):
        parent[find(b)] = find(a)

    for wire in document.get("wires", []):
        ends = []
        for role in ("source", "target"):
            endpoint = wire[role]
            key = (endpoint["component_id"], endpoint["port"])
            if key not in parent:
                raise CircuitError(f"配線先の端子が存在しません: {key[0]}.{key[1]}")
            ends.append(key)
        union(*ends)
    grounded = [key for key in parent if by_id[key[0]]["kind"].upper() == "GND"]
    if not grounded:
        raise CircuitError("回路に GND を接続してください")
    for key in grounded[1:]:
        union(grounded[0], key)
    ground = find(grounded[0])
    net_names = {ground: "0"}
    net_map = {}
    for key in parent:
        representative = find(key)
        if representative not in net_names:
            net_names[representative] = f"n{len(net_names)}"
        net_map[key] = net_names[representative]
    return by_id, ports, net_map


def _source(parameters: dict) -> str:
    result = ["DC", _number(parameters.get("dc", 0))]
    if "ac_magnitude" in parameters:
        result += ["AC", _number(parameters["ac_magnitude"], nonnegative=True), _number(parameters.get("ac_phase", 0))]
    waveform = parameters.get("waveform")
    if waveform:
        kind = waveform.get("kind", "").lower()
        if kind == "sin":
            fields = [waveform.get("offset", parameters.get("dc", 0)), waveform.get("amplitude", 1), waveform.get("frequency", 1e6), waveform.get("delay", 0), waveform.get("damping", 0), waveform.get("phase", 0)]
            result.append("SIN(" + " ".join(_number(value) for value in fields) + ")")
        elif kind == "pulse":
            fields = [waveform.get("initial", 0), waveform.get("pulsed", 1), waveform.get("delay", 0), waveform.get("rise", 1e-9), waveform.get("fall", 1e-9), waveform.get("width", 1e-6), waveform.get("period", 2e-6)]
            result.append("PULSE(" + " ".join(_number(value) for value in fields) + ")")
        elif kind == "pwl":
            points = waveform.get("points", [])
            if not 2 <= len(points) <= 10000:
                raise CircuitError("PWL は 2〜10000 個の時刻・値ペアで指定してください")
            times = [parse_si(point[0]) for point in points]
            if any(time < 0 for time in times) or any(b <= a for a, b in zip(times, times[1:])):
                raise CircuitError("PWL の時刻は非負で昇順に指定してください")
            result.append("PWL(" + " ".join(_number(v) for point in points for v in point) + ")")
        else:
            raise CircuitError(f"未対応の電源波形です: {kind}")
    return " ".join(result)


def _models(document: dict, referenced: set[str]) -> list[str]:
    definitions = {}
    for model in document.get("models", []):
        name = _identifier(model["name"], "モデル名")
        if name.upper() in definitions:
            raise CircuitError(f"モデル名が重複しています: {name}")
        definition = model["definition"].strip()
        # Accept numerical native models/subcircuits; reject commands that can
        # escape a simulation or access a server-side file.
        lines = definition.splitlines()
        if len(definition) > 100000:
            raise CircuitError("モデル定義が大きすぎます")
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("*"):
                continue
            if any(char in line for char in ("\x00", "\r", ";", "`", "\\")):
                raise CircuitError("モデル定義に制御文字は使用できません")
            directive = stripped.split()[0].lower()
            if directive.startswith(".") and directive not in {".model", ".subckt", ".ends", ".param", ".func"}:
                raise CircuitError(f"モデル内のコマンドは許可されません: {directive}")
            if re.search(r"\b(?:[A-Za-z_]*file[A-Za-z_]*|include|shell|system)\b", stripped, re.I):
                raise CircuitError("モデルからファイルや外部コマンドを参照できません")
        first = next((line.strip() for line in lines if line.strip() and not line.strip().startswith("*")), "")
        tokens = first.split()
        if len(tokens) < 3 or tokens[0].lower() not in {".model", ".subckt"} or tokens[1].upper() != name.upper():
            raise CircuitError(f"モデル定義の名前が一致しません: {name}")
        definitions[name.upper()] = definition
    result = list(definitions.values())
    for name in sorted(referenced):
        if name.upper() not in definitions:
            builtin = BUILTIN_MODELS.get(name.upper())
            if not builtin:
                raise CircuitError(f"モデル定義がありません: {name}")
            result.append(builtin)
    return result


def build_netlist(document: dict, analysis: dict | None = None) -> str:
    by_id, ports, nets = _topology(document)
    parameters = document.get("parameters", {})
    lines = ["* Plasma Circuit: " + str(document.get("name", "circuit")).replace("\n", " ").replace("\r", " ")[:200], ".options savecurrents"]
    references = set()

    def node(cid, port):
        if (cid, port) not in nets:
            raise CircuitError(f"端子が存在しません: {cid}.{port}")
        return nets[(cid, port)]

    def control(params, key="control_source", expected="V"):
        cid = str(params.get(key, ""))
        target = by_id.get(cid)
        if not target or target["kind"].upper() != expected:
            raise CircuitError(f"{key} に {expected} 素子の部品 ID を指定してください: {cid}")
        return _element_name(target)

    for cid, component in by_id.items():
        kind = component["kind"].upper()
        if kind in {"GND", "JUNCTION"}:
            continue
        params = {**_DEFAULTS[kind]["parameters"], **component.get("parameters", {})}
        name = _element_name(component)
        terminals = [node(cid, port) for port in ports[cid]]
        prefix = name + " " + " ".join(terminals)
        raw = _safe_native(params.get("raw", ""))
        if kind in {"R", "C", "L"}:
            if len(terminals) != 2:
                raise CircuitError(f"{kind} は 2 端子で指定してください")
            line = f"{prefix} {_number(params['value'], positive=True)}"
            if "initial" in params and kind in {"C", "L"}:
                line += " IC=" + _number(params["initial"])
        elif kind in {"V", "I"}:
            line = f"{prefix} {_source(params)}"
        elif kind in {"E", "G"}:
            if len(terminals) != 4:
                raise CircuitError(f"{kind} は出力端子・制御端子の 4 端子で指定してください")
            line = f"{prefix} {_number(params['gain'])}"
        elif kind in {"F", "H"}:
            line = f"{prefix} {control(params)} {_number(params['gain'])}"
        elif kind == "K":
            coupling = float(_number(params["coupling"]))
            if not -1 <= coupling <= 1:
                raise CircuitError("結合係数は -1〜1 の範囲です")
            line = f"{name} {control(params, 'inductor1', 'L')} {control(params, 'inductor2', 'L')} {coupling}"
        elif kind == "B":
            variables = {"V1": f"v({terminals[0]},{terminals[1]})"}
            expr = compile_expression(str(params["expression"]), variables=variables, parameters={**parameters, **params.get("parameters", {})}, intermediates=params.get("intermediates", {}))
            mode = params.get("mode", "current")
            if mode not in {"current", "voltage"}:
                raise CircuitError("B 電源の mode は current / voltage を指定してください")
            line = f"{prefix} {'I' if mode == 'current' else 'V'}={{{expr}}}"
        elif kind == "EDD":
            branches = params.get("branches", [])
            if not 1 <= len(branches) <= 32:
                raise CircuitError("EDD の枝数は 1〜32 です")
            variables = {}
            for index, branch in enumerate(branches, 1):
                variables[f"V{index}"] = f"v({node(cid, branch['positive'])},{node(cid, branch['negative'])})"
                variables[f"I{index}"] = f"v(edd_{cid}_i{index})"
            for index, branch in enumerate(branches, 1):
                kwargs = {"variables": variables, "parameters": {**parameters, **params.get("parameters", {})}, "intermediates": params.get("intermediates", {})}
                current = compile_expression(str(branch.get("current", "0")), **kwargs)
                charge = compile_expression(str(branch.get("charge", "0")), **kwargs)
                p, n = node(cid, branch["positive"]), node(cid, branch["negative"])
                internal = f"edd_{cid}_b{index}"
                lines += [f"B_edd_{cid}_i{index} edd_{cid}_i{index} 0 V={{{current}}}", f"B_edd_{cid}_q{index} edd_{cid}_q{index} 0 V={{{charge}}}", f"V_edd_{cid}_total{index} {p} {internal} 0", f"B_edd_{cid}_c{index} {internal} {n} I=v(edd_{cid}_i{index})"]
                if str(branch.get("charge", "0")).strip() not in {"", "0", "0.0"}:
                    lines.append(f"C_edd_{cid}_q{index} {internal} {n} Q={{{charge}}}")
            continue
        elif kind == "T":
            line = f"{prefix} Z0={_number(params['impedance'], positive=True)} TD={_number(params['delay'], positive=True)}"
        else:
            model = _identifier(params.get("model", ""), "モデル名")
            references.add(model)
            line = f"{prefix} {model}"
            if kind in {"D", "Q", "J", "Z"}:
                line += " " + _number(params.get("area", 1), positive=True)
            elif kind == "W":
                line = f"{prefix} {control(params)} {model}"
            elif kind == "U":
                line += f" L={_number(params['length'], positive=True)} N={int(params.get('lumps', 5))}"
            elif kind == "X":
                for arg, value in params.get("arguments", {}).items():
                    line += f" {_identifier(arg)}={_number(value)}"
        lines.append(line + (" " + raw if raw else ""))
    lines += _models(document, references)
    settings = (analysis or {}).get("settings", {})
    initial = settings.get("initial_conditions", {})
    if initial:
        conditions = []
        for key, value in initial.items():
            if "." in key:
                cid, port = key.split(".", 1)
                resolved = node(cid, port)
            else:
                resolved = str(key)
                if not _NODE.fullmatch(resolved):
                    raise CircuitError("初期条件のノード名が不正です")
            conditions.append(f"v({resolved})={_number(value)}")
        lines.append(".ic " + " ".join(conditions))
    lines.append(".end")
    return "\n".join(lines) + "\n"


def _analysis_settings(analysis: dict) -> tuple[str, dict]:
    kind = analysis.get("kind", "op")
    settings = dict(analysis.get("settings", {}))
    if kind not in {"op", "dc", "ac", "transient"}:
        raise CircuitError(f"回路ソルバーで扱えない解析です: {kind}")
    if kind == "transient":
        dt = float(_number(settings.get("time_step", 1e-9), positive=True))
        stop = float(_number(settings.get("stop_time", 1e-6), positive=True))
        start = float(_number(settings.get("start_time", 0), nonnegative=True))
        if start >= stop:
            raise CircuitError("開始時刻は終了時刻より小さくしてください")
        if (stop - start) / dt > MAX_REQUESTED_POINTS:
            raise CircuitError(f"要求点数が上限 {MAX_REQUESTED_POINTS} を超えます。時間刻みを大きくしてください")
        settings.update(time_step=dt, stop_time=stop, start_time=start)
        if "max_step" in settings:
            settings["max_step"] = float(_number(settings["max_step"], positive=True))
            if (stop - start) / min(dt, settings["max_step"]) > MAX_REQUESTED_POINTS:
                raise CircuitError("最大時間刻みを考慮した要求点数が上限を超えます")
    elif kind == "ac":
        start = float(_number(settings.get("start_frequency", 1), positive=True))
        stop = float(_number(settings.get("stop_frequency", 1e8), positive=True))
        points = int(settings.get("points", 50))
        variation = settings.get("variation", "dec")
        if start >= stop or not 1 <= points <= 10000 or variation not in {"dec", "lin", "oct"}:
            raise CircuitError("AC 周波数範囲・点数・variation が不正です")
        predicted = points if variation == "lin" else points * math.log(stop / start, 10 if variation == "dec" else 2)
        if predicted > MAX_REQUESTED_POINTS:
            raise CircuitError("AC 解析の要求点数が上限を超えます")
        settings.update(start_frequency=start, stop_frequency=stop, points=points, variation=variation)
    elif kind == "dc":
        start = float(_number(settings.get("start", 0)))
        stop = float(_number(settings.get("stop", 5)))
        step = float(_number(settings.get("step", 0.1)))
        if step == 0 or (stop - start) * step <= 0 or abs((stop - start) / step) > MAX_REQUESTED_POINTS:
            raise CircuitError("DC スイープの範囲・刻みが不正です")
        settings.update(start=start, stop=stop, step=step)
        _identifier(settings.get("source", ""), "スイープ電源")
    return kind, settings


def _raw_spice(netlist: str) -> str:
    lines = []
    for line in netlist.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        directive = stripped.split()[0].lower()
        if directive in {".end", ".op", ".tran", ".dc", ".ac", ".title"}:
            continue
        if directive in {".control", ".endc", ".include", ".lib", ".save", ".print", ".plot", ".exec"}:
            raise CircuitError(f"実行用ネットリストに {directive} は使用できません")
        lines.append(line)
    return "\n".join(lines) + "\n"


def _solver_version(simulator) -> dict:
    try:
        version = str(simulator._ngspice_shared.ngspice_version)
    except Exception:
        version = "unknown"
    return {"pyspice": importlib.metadata.version("PySpice"), "ngspice": version}


def simulate_netlist(netlist: str, analysis: dict) -> dict:
    """Run real ngspice through PySpice. No numerical fallback is used."""
    kind, settings = _analysis_settings(analysis)
    try:
        from PySpice.Spice.Netlist import Circuit
        from PySpice.Spice.NgSpice.Shared import NgSpiceShared
    except ImportError as exc:
        raise SolverError("PySpice がありません。計算ワーカーの依存関係を確認してください") from exc
    circuit = Circuit("Plasma Circuit")
    circuit.raw_spice = _raw_spice(netlist)
    try:
        # Runtime-only Debian packages install .so.0 without the development
        # symlink assumed by PySpice. Respect an explicit administrator path.
        if not os.environ.get("NGSPICE_LIBRARY_PATH"):
            located = ctypes.util.find_library("ngspice")
            if located:
                NgSpiceShared.LIBRARY_PATH = located
        simulator = circuit.simulator(simulator="ngspice-shared")
        if kind == "op":
            solved = simulator.operating_point()
            axis_name, axis_unit = "operating_point", ""
            axis = np.array([0.0])
        elif kind == "transient":
            kwargs = {"step_time": settings["time_step"], "end_time": settings["stop_time"], "start_time": settings["start_time"], "use_initial_condition": bool(settings.get("use_initial_condition", False))}
            if "max_step" in settings:
                kwargs["max_time"] = settings["max_step"]
            solved = simulator.transient(**kwargs)
            axis_name, axis_unit = "time", "s"
            axis = np.asarray(solved.time, dtype=float)
        elif kind == "ac":
            solved = simulator.ac(variation=settings["variation"], number_of_points=settings["points"], start_frequency=settings["start_frequency"], stop_frequency=settings["stop_frequency"])
            axis_name, axis_unit = "frequency", "Hz"
            axis = np.asarray(solved.frequency, dtype=float)
        else:
            solved = simulator.dc(**{settings["source"]: slice(settings["start"], settings["stop"], settings["step"])})
            axis_name, axis_unit = "sweep", "A" if settings["source"].lower().startswith("i") else "V"
            axis = np.asarray(solved.sweep, dtype=float)
    except Exception as exc:
        detail = ""
        if "simulator" in locals():
            detail = str(getattr(simulator._ngspice_shared, "stderr", "")).strip()[-4000:]
        raise SolverError(f"ngspice 計算が失敗しました: {exc}" + ("\n" + detail if detail else "")) from exc
    if not len(axis) or not np.isfinite(axis).all():
        raise SolverError("ngspice の結果に有効な計算点がありません")
    count = len(axis)
    if count > MAX_REQUESTED_POINTS:
        raise SolverError("ngspice の出力点数が計算上限を超えました")
    keep = np.unique(np.linspace(0, count - 1, min(count, MAX_SAMPLES), dtype=int))
    # Plasma coupling integrates instantaneous powers/current before reducing
    # its final waveforms. Decimated RF samples must never enter those means.
    vector_keep = np.arange(count) if settings.get("retain_all_samples", False) else keep
    vectors = {}
    signals = []
    rows = []
    complex_vectors = {}
    summary = {}
    device_currents = {}
    for key, waveform in solved.internal_parameters.items():
        match = re.fullmatch(r"@(.+)\[(i|id|ic|ib|ie|is|ig)\]", str(key))
        if match:
            device, quantity = match.groups()
            # Native Q capacitances expand internally into B sources; their
            # private nodes/current sources are implementation details.
            if device.startswith(("b_edd_", "c_edd_", "edd_")):
                continue
            device_currents[device if quantity == "i" else f"{device}:{quantity}"] = waveform
    groups = [(solved.nodes, "V", "V"), (solved.branches, "I", "A"), (device_currents, "I", "A")]
    unavailable_currents = []
    for group, prefix, unit in groups:
        for key, waveform in group.items():
            key = str(key)
            state = re.fullmatch(r"edd_(.+)_([iq])(\d+)", key) if prefix == "V" else None
            total = re.fullmatch(r"v_edd_(.+)_total(\d+)", key) if prefix == "I" else None
            if not state and not total and (key.startswith("edd_") or "_edd_" in key or re.search(r"_int\d+$", key) or "#" in key):
                continue
            values = np.asarray(waveform)
            if len(values) != count:
                # ngspice creates empty @device[i] vectors for devices that do
                # not export their current in a particular analysis (e.g. AC).
                unavailable_currents.append(key)
                continue
            if not np.isfinite(values).all():
                raise SolverError(f"結果 {key} に非有限値があります")
            full_values = values[vector_keep]
            values = values[keep]
            name = f"{prefix}({key})"
            signal_unit = unit
            if state:
                cid, quantity, index = state.groups()
                name = f"I({cid}:branch{index}) conductive" if quantity == "i" else f"Q({cid}:branch{index})"
                signal_unit = "A" if quantity == "i" else "C"
            if total:
                cid, index = total.groups()
                name = f"I({cid}:branch{index}) total"
            alias = key if prefix == "V" else f"i({key})"
            if alias in vectors:
                continue
            if np.iscomplexobj(values):
                magnitude = np.abs(values).astype(float).tolist()
                phase = np.angle(values, deg=True).astype(float).tolist()
                signals += [{"name": name + " magnitude", "unit": signal_unit, "values": magnitude}, {"name": name + " phase", "unit": "deg", "values": phase}]
                vectors[alias] = np.abs(full_values).astype(float).tolist()
                complex_vectors[alias] = {"real": full_values.real.astype(float).tolist(), "imag": full_values.imag.astype(float).tolist()}
            else:
                numeric = values.astype(float).tolist()
                vectors[alias] = full_values.astype(float).tolist()
                if kind == "op":
                    rows.append({"signal": name, "value": numeric[0], "unit": signal_unit})
                    summary[name] = numeric[0]
                else:
                    signals.append({"name": name, "unit": signal_unit, "values": numeric})
    stderr = str(getattr(simulator._ngspice_shared, "stderr", "")).strip()
    result = {"kind": kind, "converged": True, "summary": summary, "axis": {"name": axis_name, "unit": axis_unit, "values": axis[keep].tolist() if kind != "op" else []}, "signals": signals, "tables": [{"name": "動作点", "columns": ["signal", "value", "unit"], "rows": rows}] if rows else [], "logs": stderr.splitlines()[-100:] if stderr else [], "netlist": netlist, "solver": _solver_version(simulator), "model_metadata": {}, "diagnostics": {"original_sample_count": count, "saved_sample_count": len(keep), "internal_vector_sample_count": len(vector_keep), "decimated": count > len(keep), "decimation": "uniform index selection; first and last retained" if count > len(keep) else "none"}, "vectors": vectors, "x": axis[vector_keep].tolist()}
    if complex_vectors:
        result["complex_vectors"] = complex_vectors
    if unavailable_currents:
        result["diagnostics"]["unavailable_device_currents"] = unavailable_currents
    if count > len(keep):
        result["logs"].append(f"表示・保存点数を {count} 点から {len(keep)} 点に間引きました")
    return result


def execute_circuit(document: dict, analysis: dict) -> dict:
    kind, _ = _analysis_settings(analysis)
    resolved = {**analysis, "settings": dict(analysis.get("settings", {}))}
    resolved["settings"].pop("retain_all_samples", None)
    if kind == "dc":
        source = resolved["settings"]["source"]
        components = {c["id"]: c for c in document.get("components", [])}
        if source not in components or components[source]["kind"].upper() not in {"V", "I"}:
            raise CircuitError("DC スイープには電圧源・電流源の部品 ID を指定してください")
        resolved["settings"]["source"] = _element_name(components[source])
    netlist = build_netlist(document, resolved)
    result = simulate_netlist(netlist, resolved)
    _, _, nets = _topology(document)
    result["diagnostics"]["node_map"] = {f"{cid}.{port}": net for (cid, port), net in nets.items()}
    sample_count = len(result["x"])

    def node_values(net: str) -> np.ndarray:
        if net == "0":
            return np.zeros(sample_count, dtype=complex if kind == "ac" else float)
        if kind == "ac":
            vector = result["complex_vectors"][net]
            return np.asarray(vector["real"]) + 1j * np.asarray(vector["imag"])
        return np.asarray(result["vectors"][net], dtype=float)

    # Explicit terminal differences let Q-V and I-V plots match the EDD branch,
    # including floating branches. AC differences use complex solver vectors,
    # never a subtraction of voltage magnitudes.
    for component in document.get("components", []):
        if component["kind"].upper() != "EDD":
            continue
        cid = component["id"]
        branches = component.get("parameters", {}).get("branches", _DEFAULTS["EDD"]["parameters"]["branches"])
        for index, branch in enumerate(branches, 1):
            voltage = node_values(nets[(cid, branch["positive"])]) - node_values(nets[(cid, branch["negative"])])
            name = f"V({cid}:branch{index})"
            alias = name.lower()
            if kind == "ac":
                magnitude, phase = np.abs(voltage).tolist(), np.angle(voltage, deg=True).tolist()
                result["signals"] += [{"name": name + " magnitude", "unit": "V", "values": magnitude}, {"name": name + " phase", "unit": "deg", "values": phase}]
                result["vectors"][alias] = magnitude
                result["complex_vectors"][alias] = {"real": voltage.real.tolist(), "imag": voltage.imag.tolist()}
            else:
                values = voltage.tolist()
                result["vectors"][alias] = values
                if kind == "op":
                    result["summary"][name] = values[0]
                    result["tables"][0]["rows"].append({"signal": name, "value": values[0], "unit": "V"})
                else:
                    result["signals"].append({"name": name, "unit": "V", "values": values})
    result["model_metadata"] = {"edd_convention": "Ik is conductive current; terminal current = Ik + dQk/dt", "models": document.get("models", []), "parameters": document.get("parameters", {})}
    return result

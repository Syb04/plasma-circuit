"""Starter circuits and agreed CCP benchmark conditions (SI units)."""
from __future__ import annotations

from copy import deepcopy
from .coax import DEFAULT_COAX_PARAMETERS


def component(id: str, kind: str, x: float, y: float, parameters: dict, ports: list[str] | None = None) -> dict:
    return {"id": id, "kind": kind, "label": id.upper(), "ports": ports or (["g"] if kind == "GND" else ["p", "n"]), "parameters": parameters, "position": {"x": x, "y": y}, "rotation": 90 if kind in {"V", "I"} else 0}


def wire(id: str, source: str, source_port: str, target: str, target_port: str) -> dict:
    return {"id": id, "source": {"component_id": source, "port": source_port}, "target": {"component_id": target, "port": target_port}}


def document(name: str, description: str, components: list[dict], wires: list[dict], parameters: dict | None = None) -> dict:
    return {"schema_version": 1, "name": name, "description": description, "components": components, "wires": wires, "parameters": parameters or {}, "models": []}


_DIVIDER = document(
    "抵抗分圧回路", "5 Vを1 kΩと2 kΩで分圧する検証回路です。出力の期待値は約3.333 Vです。",
    [component("v1", "V", 80, 100, {"dc": 5}), component("r1", "R", 330, 100, {"value": 1000}), component("r2", "R", 330, 300, {"value": 2000}), component("gnd", "GND", 80, 300, {})],
    [wire("w1", "v1", "p", "r1", "p"), wire("w2", "r1", "n", "r2", "p"), wire("w3", "r2", "n", "gnd", "g"), wire("w4", "v1", "n", "gnd", "g")],
)

_RC = document(
    "RCローパスフィルター", "R=1 kΩ、C=1 µF。遮断周波数は約159.15 Hzです。",
    [component("v1", "V", 80, 100, {"dc": 0, "ac_magnitude": 1, "ac_phase": 0, "waveform": {"kind": "sin", "offset": 0, "amplitude": 1, "frequency": 100}}), component("r1", "R", 330, 100, {"value": 1000}), component("c1", "C", 330, 300, {"value": 1e-6}), component("gnd", "GND", 80, 300, {})],
    [wire("w1", "v1", "p", "r1", "p"), wire("w2", "r1", "n", "c1", "p"), wire("w3", "c1", "n", "gnd", "g"), wire("w4", "v1", "n", "gnd", "g")],
)

_EDD = document(
    "非線形電荷EDD", "I1=V1/R、Q1=C0*V1+alpha*V1^3。伝導電流と電荷による電流を同時に計算します。",
    [component("v1", "V", 80, 100, {"dc": 0, "ac_magnitude": 1, "waveform": {"kind": "sin", "offset": 0, "amplitude": 5, "frequency": 1000}}), component("r1", "R", 330, 100, {"value": 100}), component("edd1", "EDD", 330, 300, {"branches": [{"positive": "p1", "negative": "n1", "current": "V1/R", "charge": "C0*V1+alpha*V1^3"}], "parameters": {"R": 1000, "C0": 1e-6, "alpha": 1e-7}, "intermediates": {}}, ["p1", "n1"]), component("gnd", "GND", 80, 300, {})],
    [wire("w1", "v1", "p", "r1", "p"), wire("w2", "r1", "n", "edd1", "p1"), wire("w3", "edd1", "n1", "gnd", "g"), wire("w4", "v1", "n", "gnd", "g")],
)

_COAX = document(
    "同軸ケーブル — 40 MHz", "約50 Ω・1 mの同軸を50 Ω電源と負荷に接続。寸法・材料と基準周波数の損失を変更できます。",
    [component("v1", "V", 70, 120, {"dc": 0, "ac_magnitude": 1, "waveform": {"kind": "sin", "offset": 0, "amplitude": 1, "frequency": 40e6}}),
     component("source_r", "R", 240, 120, {"value": 50}),
     component("coax1", "COAX", 440, 120, DEFAULT_COAX_PARAMETERS.copy(), ["p1", "n1", "p2", "n2"]),
     component("load", "R", 650, 120, {"value": 50}), component("gnd", "GND", 70, 330, {})],
    [wire("w1", "v1", "p", "source_r", "p"), wire("w2", "source_r", "n", "coax1", "p1"),
     wire("w3", "coax1", "p2", "load", "p"), wire("w4", "load", "n", "coax1", "n2"),
     wire("w5", "coax1", "n1", "gnd", "g"), wire("w6", "v1", "n", "gnd", "g")],
)

_COAX_GND = document(
    "同軸ケーブル（シールド接地）— 40 MHz", "シールドを内部でGNDに接続した2端子の同軸。約50 Ω・1 mを50 Ω電源と負荷に接続。",
    [component("v1", "V", 70, 120, {"dc": 0, "ac_magnitude": 1, "waveform": {"kind": "sin", "offset": 0, "amplitude": 1, "frequency": 40e6}}),
     component("source_r", "R", 240, 120, {"value": 50}),
     component("coax1", "COAX_GND", 440, 120, DEFAULT_COAX_PARAMETERS.copy(), ["p1", "p2"]),
     component("load", "R", 650, 120, {"value": 50}), component("gnd", "GND", 70, 330, {})],
    [wire("w1", "v1", "p", "source_r", "p"), wire("w2", "source_r", "n", "coax1", "p1"),
     wire("w3", "coax1", "p2", "load", "p"), wire("w4", "load", "n", "gnd", "g"),
     wire("w6", "v1", "n", "gnd", "g")],
)

CCP_SETTINGS = {
    "gas": "Ar", "frequency_hz": 40e6, "rf_peak_voltage": 250,
    "pressure_pa": 1.3332236842105263, "gap_m": 0.05,
    "gas_temperature_k": 300, "cathode_diameter_m": 0.3, "area_ratio": 5,
    "electron_density_m3": 1e16, "electron_temperature_ev": 3,
}


def get_presets() -> dict:
    presets = [
        {"id": "divider", "name": _DIVIDER["name"], "description": _DIVIDER["description"], "document": _DIVIDER, "analysis": {"kind": "op", "settings": {}}},
        {"id": "rc", "name": _RC["name"], "description": _RC["description"], "document": _RC, "analysis": {"kind": "ac", "settings": {"start_frequency": 1, "stop_frequency": 100000, "points": 40, "variation": "dec"}}},
        {"id": "edd", "name": _EDD["name"], "description": _EDD["description"], "document": _EDD, "analysis": {"kind": "transient", "settings": {"time_step": 2e-6, "stop_time": 0.005, "max_step": 2e-6}}},
        {"id": "coax", "name": _COAX["name"], "description": _COAX["description"], "document": _COAX, "analysis": {"kind": "transient", "settings": {"time_step": .1e-9, "stop_time": .5e-6, "max_step": .1e-9}}},
        {"id": "coax_grounded", "name": _COAX_GND["name"], "description": _COAX_GND["description"], "document": _COAX_GND, "analysis": {"kind": "transient", "settings": {"time_step": .1e-9, "stop_time": .5e-6, "max_step": .1e-9}}},
    ]
    for gas in ("Ar", "O2"):
        name = f"{gas} CCP — 40 MHz / 250 Vpeak"
        description = "合意した条件の設定専用CCPモデル。回路図編集は通常の回路プリセットで行います。固定密度・温度は入力値です。"
        doc = document(name, description, [], [], {"builtin_ccp_template": 1.0})
        settings = {**CCP_SETTINGS, "gas": gas}
        presets.append({"id": f"ccp-{gas.lower()}", "name": name, "description": description, "document": doc, "analysis": {"kind": "ccp", "settings": settings}})
    from .external_rf import template_document
    from .plasma import CCPSettings
    external_settings = {**CCP_SETTINGS, "cycles": 80, "points_per_cycle": 256}
    external_document = template_document(CCPSettings.parse(external_settings), {
        "source_resistance_ohm": 50, "series_inductance_h": 1e-7,
        "dc_block_capacitance_f": 1e-9, "shunt_capacitance_f": 1e-11})
    external_document.update(name="Ar CCP — 外部RF・整合・DCブロック",
                             description="250 VpeakのRF電源、50 Ω出力抵抗、100 nH直列整合、1 nF DCブロック、10 pF寄生容量。電極電圧・自己バイアスを回路から計算します。")
    presets.append({"id": "ccp-external-rf", "name": external_document["name"], "description": external_document["description"],
                    "document": external_document, "analysis": {"kind": "ccp", "settings": external_settings}})
    return {"presets": deepcopy(presets)}

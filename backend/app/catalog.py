"""Native ngspice device metadata and reproducible built-in models."""
from __future__ import annotations

from copy import deepcopy

from .diode import DEFAULT_DIODE_PARAMETERS
from .coax import DEFAULT_COAX_PARAMETERS

BUILTIN_MODELS = {
    "DDEFAULT": ".model DDEFAULT D(Is=1e-14 N=1 Rs=0.1 Cjo=1e-12)",
    "NPNDEFAULT": ".model NPNDEFAULT NPN(Is=1e-15 Bf=100 Vaf=100)",
    "PNPDEFAULT": ".model PNPDEFAULT PNP(Is=1e-15 Bf=100 Vaf=100)",
    "NJFETDEFAULT": ".model NJFETDEFAULT NJF(Vto=-2 Beta=1e-3 Lambda=0.01)",
    "NMESDEFAULT": ".model NMESDEFAULT NMF(Vto=-2 Beta=1e-3)",
    "SWDEFAULT": ".model SWDEFAULT SW(Ron=1 Roff=1e12 Vt=0 Vh=0)",
    "CSWDEFAULT": ".model CSWDEFAULT CSW(Ron=1 Roff=1e12 It=0 Ih=0)",
    "LTRDEFAULT": ".model LTRDEFAULT LTRA(R=1 L=1e-6 G=0 C=1e-10 LEN=1)",
    "URCDEFAULT": ".model URCDEFAULT URC(RPERL=100 CPERL=1e-10)",
    "TXLDEFAULT": ".model TXLDEFAULT TXL(R=1 L=1e-6 G=0 C=1e-10 LENGTH=1)",
    "CPLDEFAULT": ".model CPLDEFAULT CPL(R=1 L=1e-6 G=0 C=1e-10 LENGTH=1)",
}


def _entry(kind, label, category, ports, parameters, description=""):
    return {"kind": kind, "label": label, "category": category, "ports": ports,
            "parameters": parameters, "description": description}


COMPONENTS = [
    _entry("R", "抵抗", "受動素子", ["p", "n"], {"value": 1000}),
    _entry("C", "コンデンサ", "受動素子", ["p", "n"], {"value": 1e-9}),
    _entry("L", "インダクタ", "受動素子", ["p", "n"], {"value": 1e-6}),
    _entry("K", "インダクタ結合", "受動素子", [], {"inductor1": "l1", "inductor2": "l2", "coupling": 0.99}),
    _entry("V", "電圧源", "電源", ["p", "n"], {"dc": 5, "ac_magnitude": 1, "ac_phase": 0}),
    _entry("I", "電流源", "電源", ["p", "n"], {"dc": 0.001, "ac_magnitude": 0, "ac_phase": 0}),
    _entry("E", "電圧制御電圧源", "従属電源", ["p", "n", "cp", "cn"], {"gain": 1}),
    _entry("F", "電流制御電流源", "従属電源", ["p", "n"], {"control_source": "v1", "gain": 1}),
    _entry("G", "電圧制御電流源", "従属電源", ["p", "n", "cp", "cn"], {"gain": 0.001}),
    _entry("H", "電流制御電圧源", "従属電源", ["p", "n"], {"control_source": "v1", "gain": 1000}),
    _entry("B", "数式電源", "従属電源", ["p", "n"], {"mode": "current", "expression": "V1/1000", "parameters": {}}, "V1 は自身の端子間電圧。文書パラメータと time を参照できます。"),
    _entry("S", "電圧制御スイッチ", "スイッチ", ["p", "n", "cp", "cn"], {"model": "SWDEFAULT"}),
    _entry("W", "電流制御スイッチ", "スイッチ", ["p", "n"], {"control_source": "v1", "model": "CSWDEFAULT"}),
    _entry("D", "ダイオード", "半導体", ["p", "n"], {"model": "DDEFAULT", "area": 1}),
    _entry("D", "ダイオード（詳細設定）", "半導体", ["p", "n"], {"area": 1, "model_parameters": DEFAULT_DIODE_PARAMETERS.copy()}, "IS・N・BV・IBV・RS・接合容量・走行時間・温度依存を部品ごとに設定します。"),
    _entry("Q", "BJT", "半導体", ["c", "b", "e"], {"model": "NPNDEFAULT", "area": 1}),
    _entry("J", "JFET", "半導体", ["d", "g", "s"], {"model": "NJFETDEFAULT", "area": 1}),
    _entry("Z", "MESFET", "半導体", ["d", "g", "s"], {"model": "NMESDEFAULT", "area": 1}),
    _entry("T", "無損失伝送線", "伝送線", ["p1", "n1", "p2", "n2"], {"impedance": 50, "delay": 1e-9}),
    _entry("COAX", "同軸ケーブル", "伝送線", ["p1", "n1", "p2", "n2"], DEFAULT_COAX_PARAMETERS.copy(), "寸法・誘電体・導体抵抗率からTEM線路を生成。表皮効果・誘電体損失を基準周波数で合わせた受動RLC近似。"),
    _entry("COAX_GND", "同軸ケーブル（シールド接地）", "伝送線", ["p1", "p2"], DEFAULT_COAX_PARAMETERS.copy(), "内部導体の入力p1・出力p2の2端子。シールドは内部でGNDに接続。寸法・材料・損失モデルは4端子版と共通。"),
    _entry("O", "損失伝送線 (LTRA)", "伝送線", ["p1", "n1", "p2", "n2"], {"model": "LTRDEFAULT"}),
    _entry("U", "分布 RC 線", "伝送線", ["p1", "p2", "reference"], {"model": "URCDEFAULT", "length": 1, "lumps": 5}),
    _entry("Y", "損失伝送線 (TXL)", "伝送線", ["p1", "n1", "p2", "n2"], {"model": "TXLDEFAULT"}),
    _entry("P", "多導体伝送線 (CPL)", "伝送線", ["p1", "n1", "p2", "n2"], {"model": "CPLDEFAULT"}, "初期値は 1 導体です。複数導体では入力導体群・入力基準・出力導体群・出力基準の順に端子を追加し、R/L/G/C 行列の次元を合わせてください。"),
    _entry("X", "サブ回路", "モデル", ["p", "n"], {"model": "SUBCKT", "arguments": {}}, "文書の models に .subckt 定義を登録し端子順を一致させます。"),
    _entry("EDD", "Equation-defined device", "モデル", ["p1", "n1"], {"branches": [{"positive": "p1", "negative": "n1", "current": "V1/R", "charge": "C0*V1"}], "parameters": {"R": 1000, "C0": 1e-9}, "intermediates": {}}, "各枝の総電流は I + dQ/dt。Ik は導電電流です。"),
    _entry("PLASMA", "CCP プラズマ", "モデル", ["p", "n"], {"electron_density_m3": 1e16, "electron_temperature_ev": 3}, "pは駆動電極、nは帰還電極。外部RF回路と非線形シース・Drudeバルクを同時解析します。"),
    _entry("GND", "GND", "接続", ["g"], {}),
    _entry("JUNCTION", "接続点", "接続", ["p"], {}),
]


def get_catalog() -> dict:
    return {"components": deepcopy(COMPONENTS), "analyses": [
        {"kind": "op", "label": "DC 動作点", "settings": {}},
        {"kind": "dc", "label": "DC スイープ", "settings": {"source": "v1", "start": 0, "stop": 5, "step": 0.1}},
        {"kind": "ac", "label": "AC 小信号", "settings": {"start_frequency": 1, "stop_frequency": 1e8, "points": 50, "variation": "dec"}},
        {"kind": "transient", "label": "過渡解析", "settings": {"time_step": 1e-9, "stop_time": 1e-6}},
        {"kind": "ccp", "label": "CCP 固定密度・温度", "settings": {}},
        {"kind": "global", "label": "CCP グローバルモデル連成", "settings": {}},
    ], "builtin_models": [{"name": k, "definition": v} for k, v in BUILTIN_MODELS.items()]}


component_catalog = get_catalog

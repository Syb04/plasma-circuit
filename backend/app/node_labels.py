"""Display labels anchored to explicit circuit endpoints, never SPICE net names."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .catalog import COMPONENTS

_PORTS: dict[str, list[str]] = {}
for _entry in COMPONENTS:
    _PORTS.setdefault(_entry["kind"], _entry["ports"])


def label_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("ノード名は文字列で指定してください")
    name = value.strip()
    if not name or len(name) > 100 or any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in name):
        raise ValueError("ノード名は制御文字・改行を含まない1〜100文字で指定してください")
    return name


def endpoint_groups(document: dict) -> dict[tuple[str, str], str]:
    """Electrical groups without requiring a complete, grounded simulation graph."""
    parent = {}
    parts = document.get("components", [])
    for part in parts:
        for port in part.get("ports") or _PORTS.get(part["kind"].upper(), []):
            key = (part["id"], port)
            parent[key] = key

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b):
        if a in parent and b in parent:
            parent[find(b)] = find(a)

    for wire in document.get("wires", []):
        a, b = wire["source"], wire["target"]
        union((a["component_id"], a["port"]), (b["component_id"], b["port"]))
    grounds = []
    for part in parts:
        cid, kind = part["id"], part["kind"].upper()
        if kind == "COAX":
            union((cid, "n1"), (cid, "n2"))
        if kind == "GND":
            grounds.extend(key for key in parent if key[0] == cid)
    for key in grounds[1:]:
        union(grounds[0], key)
    return {key: ".".join(find(key)) for key in parent}


def resolve_node_labels(document: dict, nets: dict[tuple[str, str], str]) -> dict[str, str]:
    labels, anchors, owners = {}, set(), {}
    for item in document.get("node_labels", []):
        endpoint = (item["component_id"], item["port"])
        if endpoint not in nets:
            raise ValueError(f"ノード名を付ける端子が存在しません: {endpoint[0]}.{endpoint[1]}")
        if endpoint in anchors:
            raise ValueError("同じ端子に複数のノード名は指定できません")
        anchors.add(endpoint)
        name, net = label_name(item["name"]), nets[endpoint]
        if net in labels and labels[net] != name:
            raise ValueError("同じ電気的ノードに異なる名前があります。名前を統一または解除してください")
        if name in owners and owners[name] != net:
            raise ValueError(f"別の電気的ノードに同じ名前は指定できません: {name}")
        labels[net], owners[name] = name, net
    return labels


def apply_node_labels(result: dict, document: dict, nets: dict[tuple[str, str], str]) -> None:
    labels = resolve_node_labels(document, nets)
    if not labels:
        return
    result.setdefault("diagnostics", {})["node_labels"] = labels
    signals = result.get("signals", [])
    # ngspice does not export a ground vector; an explicitly named ground is 0 V.
    count = len(result.get("axis", {}).get("values", []))
    if "0" in labels and count and not any(s["name"].startswith("V(0)") for s in signals):
        suffix = " magnitude" if result.get("kind") == "ac" else ""
        signals.append({"name": "V(0)" + suffix, "unit": "V", "values": [0.0] * count})
    candidates = []
    for signal in signals:
        match = re.fullmatch(r"V\(([^()]+)\)( magnitude| phase)?", signal["name"])
        display = (f"V({labels[match[1]]}){match[2] or ''}" if match and match[1] in labels else signal["name"])
        candidates.append(display)
    # A user name can also coincide with an unlabeled or synthetic signal name.
    counts = Counter(candidates)
    for signal, display in zip(signals, candidates):
        if display != signal["name"] or counts[display] > 1:
            signal["display_name"] = display + (f" [{signal['name']}]" if counts[display] > 1 else "")

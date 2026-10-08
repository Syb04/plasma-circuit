"""Numeric, per-device ngspice diode model parameters."""
from __future__ import annotations

import math

from .expressions import parse_si

DEFAULT_DIODE_PARAMETERS = {"IS": 1e-14, "N": 1, "RS": 0.1, "CJO": 1e-12}
POSITIVE = {"IS", "N", "BV", "IBV", "VJ", "EG"}
NONNEGATIVE = {"RS", "CJO", "M", "TT"}
ALLOWED = POSITIVE | NONNEGATIVE | {"XTI", "FC", "TNOM"}


def diode_model(name: str, parameters: dict) -> str:
    if not isinstance(parameters, dict):
        raise ValueError("ダイオード model_parameters はオブジェクトで指定してください")
    normalized = {}
    for key, value in parameters.items():
        parameter = str(key).upper()
        if parameter not in ALLOWED:
            raise ValueError(f"未対応のダイオードモデルパラメータです: {key}")
        if parameter in normalized:
            raise ValueError(f"ダイオードモデルパラメータが重複しています: {key}")
        try:
            if isinstance(value, bool):
                raise ValueError()
            number = parse_si(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"ダイオード {parameter} の数値が不正です: {value}") from exc
        if (not math.isfinite(number)
                or (parameter in POSITIVE and number <= 0)
                or (parameter in NONNEGATIVE and number < 0)
                or (parameter == "FC" and not 0 <= number < 1)
                or (parameter == "TNOM" and number <= -273.15)):
            raise ValueError(f"ダイオード {parameter} の数値の範囲が不正です: {value}")
        normalized[parameter] = number
    body = " ".join(f"{key}={value:.16g}" for key, value in normalized.items())
    return f".model {name} D({body})"

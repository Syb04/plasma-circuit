"""Portable, hash-checked records; imported results remain historical evidence."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from . import database as db
from .schemas import Analysis, CircuitDocument

MAX_PACKAGE_BYTES = 33554432
HASH_ALGORITHM = "sha256-json-binary64-v1"


def encoded_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      allow_nan=False, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    """Hash JSON numbers by their binary64 value, independent of spelling.

    JavaScript parses JSON numbers as IEEE 754 doubles and writes 1.0 as 1,
    -0.0 as 0, and large doubles using a different decimal/exponent spelling.
    Seventeen significant digits identify the same finite binary64 value after
    either serialization. This also preserves JSON booleans as a distinct type.
    """
    chunks = []
    def encode(item: Any) -> None:
        if item is None:
            chunks.append("null")
        elif isinstance(item, bool):
            chunks.append("true" if item else "false")
        elif isinstance(item, (int, float)):
            try:
                number = float(item)
            except OverflowError as exc:
                raise ValueError("Package numbers must fit finite IEEE 754 binary64 values") from exc
            if not math.isfinite(number):
                raise ValueError("Package numbers must fit finite IEEE 754 binary64 values")
            chunks.append("0" if number == 0 else format(number, ".17g"))
        elif isinstance(item, str):
            chunks.append(json.dumps(item, ensure_ascii=False))
        elif isinstance(item, list):
            chunks.append("[")
            for index, child in enumerate(item):
                if index:
                    chunks.append(",")
                encode(child)
            chunks.append("]")
        elif isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("Package object keys must be strings")
            chunks.append("{")
            for index, key in enumerate(sorted(item)):
                if index:
                    chunks.append(",")
                chunks.append(json.dumps(key, ensure_ascii=False) + ":")
                encode(item[key])
            chunks.append("}")
        else:
            raise ValueError("Package content must be JSON data")
    encode(value)
    return hashlib.sha256("".join(chunks).encode()).hexdigest()


def legacy_digest(value: Any) -> str:
    return hashlib.sha256(encoded_json(value)).hexdigest()


def content_hashes(package: dict, hash_value=digest) -> dict:
    return {
        "input_sha256": hash_value(package["input"]),
        "model_sha256": hash_value({"models": package["input"]["document"].get("models", []),
                                "model_metadata": (package.get("result") or {}).get("model_metadata", {})}),
        "source_sha256": hash_value(package["runtime_config"].get("implementation_sha256", {})),
        "result_sha256": hash_value(package.get("result")),
    }


def export_run(run: db.SimulationRun) -> dict:
    package = {"format": "plasma-circuit-analysis", "format_version": 1, "hash_algorithm": HASH_ALGORITHM,
               "exported_at": db.utcnow().isoformat(), "original_run_id": run.id,
               "original_status": run.status, "original_error": run.error,
               "input": {"document": db.json_copy(run.snapshot), "analysis": db.json_copy(run.analysis),
                         "circuit_id": run.circuit_id, "circuit_revision": run.circuit_revision},
               "runtime_config": db.json_copy(run.runtime_config),
               "result": db.decompress_result(run.result_compressed),
               "interpretation": "Historical calculation record; convergence alone does not establish physical validation"}
    package["hashes"] = content_hashes(package)
    package["package_sha256"] = digest(package)
    if len(encoded_json(package)) > MAX_PACKAGE_BYTES:
        raise ValueError("Analysis package exceeds the 32 MiB limit; reduce saved result points")
    return package


def verify_package(value: dict) -> dict:
    package = db.json_copy(value)
    if len(encoded_json(package)) > MAX_PACKAGE_BYTES:
        raise ValueError("Analysis package exceeds the 32 MiB limit")
    if package.get("format") != "plasma-circuit-analysis" or package.get("format_version") != 1:
        raise ValueError("Unsupported analysis package format")
    algorithm = package.get("hash_algorithm")
    if algorithm is not None and algorithm != HASH_ALGORITHM:
        raise ValueError("Unsupported analysis package hash algorithm")
    hash_value = legacy_digest if algorithm is None else digest
    claimed = package.pop("package_sha256", None)
    if not isinstance(claimed, str) or hash_value(package) != claimed:
        raise ValueError("Analysis package checksum mismatch")
    try:
        if not isinstance(package["input"], dict):
            raise ValueError("Analysis package input must be an object")
        result = package.get("result")
        if result is not None and not isinstance(result, dict):
            raise ValueError("Analysis package result must be an object or null")
        if result is not None and not isinstance(result.get("model_metadata", {}), dict):
            raise ValueError("Analysis package model metadata must be an object")
        CircuitDocument.model_validate(package["input"]["document"])
        Analysis.model_validate(package["input"]["analysis"])
        if not isinstance(package["runtime_config"], dict) or not isinstance(package["original_run_id"], str):
            raise ValueError("Invalid provenance")
        if package.get("hashes") != content_hashes(package, hash_value):
            raise ValueError("Analysis package content hashes do not match")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Analysis package is missing required content") from exc
    package["package_sha256"] = claimed
    return package

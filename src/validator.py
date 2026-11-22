"""Deterministic rules that compare SRS requirements with OpenAPI operations.

No language model is used here. Every check is an exact comparison, so the same
input always gives the same findings. The LLM in rag.py only explains findings
that these rules have already established.

Rules
    V01 ENDPOINT_MISMATCH        endpoint path differs
    V02 METHOD_MISMATCH          HTTP method differs
    V03 STATUS_CODE_MISMATCH     expected success code is not among the API's responses
    V04 MISSING_OPERATION        no operation carries the requirement's x-requirement-id
    V05 REQUIRED_FIELD_MISMATCH  a required field (name + location) is missing from the API
    V06 FIELD_TYPE_MISMATCH      a required field exists in both but its type differs
"""
from __future__ import annotations

import json
from typing import Any

RULES = {
    "V01": "ENDPOINT_MISMATCH",
    "V02": "METHOD_MISMATCH",
    "V03": "STATUS_CODE_MISMATCH",
    "V04": "MISSING_OPERATION",
    "V05": "REQUIRED_FIELD_MISMATCH",
    "V06": "FIELD_TYPE_MISMATCH",
}

RULE_LABELS = {
    "ENDPOINT_MISMATCH": "Endpoint mismatch",
    "METHOD_MISMATCH": "Method mismatch",
    "STATUS_CODE_MISMATCH": "Status code mismatch",
    "MISSING_OPERATION": "Missing operation",
    "REQUIRED_FIELD_MISMATCH": "Required field mismatch",
    "FIELD_TYPE_MISMATCH": "Field type mismatch",
}


def _normalise_path(path: str) -> str:
    path = (path or "").strip()
    return path.rstrip("/") or "/"


def _field_str(field: dict) -> str:
    return f"{field['name']}:{field['location']}:{field['type']}"


def _key(field: dict) -> tuple[str, str]:
    return field["name"], field["location"]


def _finding(rule: str, req: dict, op: dict | None, expected: str, actual: str,
             message: str, context: str | None = None) -> dict[str, Any]:
    sources = [f"{req['source']}:{req['requirement_id']}"]
    if op is not None:
        sources.append(f"{op['source']}:{op['operation_id']}")
    finding = {
        "finding_id": "",  # numbered after all checks run
        "requirement_id": req["requirement_id"],
        "rule": rule,
        "expected": expected,
        "actual": actual,
        "severity": "ERROR",
        "message": message,
        "sources": sources,
    }
    if context:
        finding["context"] = context
    return finding


def compare_fields(req: dict, op: dict) -> list[dict[str, Any]]:
    """Apply V05 (missing required field) and V06 (type mismatch) to one pair."""
    findings = []
    api_fields = {_key(f): f for f in op["required_fields"]}
    srs_keys = {_key(f) for f in req["required_fields"]}
    unexpected = [f for k, f in api_fields.items() if k not in srs_keys]

    for field in req["required_fields"]:
        api_field = api_fields.get(_key(field))
        if api_field is None:
            context = None
            if unexpected:
                context = ("The API requires field(s) not in the SRS: "
                           + ", ".join(_field_str(f) for f in unexpected))
            actual = ", ".join(_field_str(f) for f in unexpected) if unexpected else "Missing"
            findings.append(_finding(
                "REQUIRED_FIELD_MISMATCH", req, op, _field_str(field), actual,
                f"Required {field['location']} field '{field['name']}' is not required by "
                f"operation {op['operation_id']}.", context))
        elif api_field["type"] != field["type"]:
            findings.append(_finding(
                "FIELD_TYPE_MISMATCH", req, op, _field_str(field), _field_str(api_field),
                f"Field '{field['name']}' is {field['type']} in the SRS but "
                f"{api_field['type']} in operation {op['operation_id']}."))
    return findings


def validate_documents(requirements: list[dict], api_operations: list[dict]) -> list[dict[str, Any]]:
    """Compare each requirement with the operation that has the same x-requirement-id.

    Matching by requirement ID (not by path) means a wrong path is reported as an
    endpoint mismatch instead of a missing operation.
    """
    by_id: dict[str, dict] = {}
    for op in api_operations:
        if op.get("requirement_id") and op["requirement_id"] not in by_id:
            by_id[op["requirement_id"]] = op

    findings: list[dict[str, Any]] = []
    for req in requirements:
        op = by_id.get(req["requirement_id"])
        if op is None:
            findings.append(_finding(
                "MISSING_OPERATION", req, None, f"{req['method']} {req['path']}", "Missing",
                f"No API operation has x-requirement-id {req['requirement_id']}."))
            continue

        if _normalise_path(req["path"]) != _normalise_path(op["path"]):
            findings.append(_finding(
                "ENDPOINT_MISMATCH", req, op, req["path"], op["path"],
                f"The SRS endpoint is {req['path']} but operation {op['operation_id']} "
                f"uses {op['path']}."))

        if req["method"].upper() != op["method"].upper():
            findings.append(_finding(
                "METHOD_MISMATCH", req, op, req["method"].upper(), op["method"].upper(),
                f"The SRS method is {req['method'].upper()} but operation "
                f"{op['operation_id']} uses {op['method'].upper()}."))

        # Only the expected success code must be present; extra codes such as 400/404 are fine.
        if str(req["success_status"]) not in op["responses"]:
            success_codes = [c for c in op["responses"] if c.startswith("2")] or op["responses"]
            findings.append(_finding(
                "STATUS_CODE_MISMATCH", req, op, str(req["success_status"]),
                ", ".join(success_codes) or "None",
                f"Expected success status {req['success_status']} is not defined for "
                f"operation {op['operation_id']}."))

        findings.extend(compare_fields(req, op))

    for number, finding in enumerate(findings, start=1):
        finding["finding_id"] = f"F{number:03d}"
    return findings


def findings_to_json(findings: list[dict[str, Any]]) -> str:
    return json.dumps(findings, indent=2)


def score_against_ground_truth(findings: list[dict], ground_truth: list[dict]) -> dict[str, Any]:
    """Match findings to known errors by (requirement_id, rule) and compute precision/recall."""
    reported = {(f["requirement_id"], f["rule"]) for f in findings}
    known = {(g["requirement_id"], g["rule"]) for g in ground_truth}
    correct = reported & known
    return {
        "known_errors": len(known),
        "reported_findings": len(reported),
        "correctly_detected": len(correct),
        "false_positives": sorted(reported - known),
        "missed_errors": sorted(known - reported),
        "precision": len(correct) / len(reported) if reported else 1.0,
        "recall": len(correct) / len(known) if known else 1.0,
    }

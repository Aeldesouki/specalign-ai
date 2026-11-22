"""Tests for the parsers and each validation rule (V01-V06)."""
import copy

import pytest

from src.parsers import ParseError, parse_field_list
from src.validator import score_against_ground_truth, validate_documents


def make_req(**overrides):
    req = {
        "source": "requirements.docx", "requirement_id": "REQ-001", "title": "Create Order",
        "description": "", "method": "POST", "path": "/api/orders", "success_status": "201",
        "required_fields": [{"name": "customerId", "location": "body", "type": "string"},
                            {"name": "items", "location": "body", "type": "array"}],
    }
    req.update(overrides)
    return req


def make_op(**overrides):
    op = {
        "source": "openapi.yaml", "requirement_id": "REQ-001", "operation_id": "createOrder",
        "summary": "", "method": "POST", "path": "/api/orders", "responses": ["201", "400"],
        "required_fields": [{"name": "customerId", "location": "body", "type": "string"},
                            {"name": "items", "location": "body", "type": "array"}],
    }
    op.update(overrides)
    return op


def rules(findings):
    return [f["rule"] for f in findings]


# ---------- Parsers ----------

def test_parse_field_list():
    assert parse_field_list("orderId:path:string; status:body:string") == [
        {"name": "orderId", "location": "path", "type": "string"},
        {"name": "status", "location": "body", "type": "string"},
    ]


@pytest.mark.parametrize("bad", ["orderId:path", "orderId:header:string", "::"])
def test_parse_field_list_rejects_bad_notation(bad):
    with pytest.raises(ParseError):
        parse_field_list(bad)


def test_parsers_read_sample_documents(baseline, faulty):
    reqs, ops = baseline
    assert len(reqs) == 10 and len(ops) == 10
    assert len(faulty[0]) == 10 and len(faulty[1]) == 9
    assert {r["requirement_id"] for r in reqs} == {f"REQ-{n:03d}" for n in range(1, 11)}
    create = next(o for o in ops if o["requirement_id"] == "REQ-001")
    assert create["required_fields"] == reqs[0]["required_fields"]


# ---------- Matching ----------

def test_matching_requirement_and_operation_gives_no_findings():
    assert validate_documents([make_req()], [make_op()]) == []


def test_matching_uses_requirement_id_not_path():
    """A wrong path must be an endpoint mismatch, not a missing operation."""
    findings = validate_documents([make_req()], [make_op(path="/api/order")])
    assert rules(findings) == ["ENDPOINT_MISMATCH"]


def test_trailing_slash_is_not_a_mismatch():
    assert validate_documents([make_req()], [make_op(path="/api/orders/")]) == []


# ---------- Rules ----------

def test_v01_endpoint_mismatch():
    [f] = validate_documents([make_req()], [make_op(path="/api/order")])
    assert (f["rule"], f["expected"], f["actual"]) == ("ENDPOINT_MISMATCH", "/api/orders", "/api/order")
    assert f["sources"] == ["requirements.docx:REQ-001", "openapi.yaml:createOrder"]


def test_v02_method_mismatch():
    [f] = validate_documents([make_req(method="GET")], [make_op(method="POST")])
    assert (f["rule"], f["expected"], f["actual"]) == ("METHOD_MISMATCH", "GET", "POST")


def test_v03_status_code_mismatch():
    [f] = validate_documents([make_req(success_status="200")], [make_op(responses=["204", "404"])])
    assert (f["rule"], f["expected"], f["actual"]) == ("STATUS_CODE_MISMATCH", "200", "204")


def test_v03_extra_error_responses_are_not_flagged():
    assert validate_documents([make_req()], [make_op(responses=["201", "400", "404", "500"])]) == []


def test_v04_missing_operation():
    [f] = validate_documents([make_req(requirement_id="REQ-004")], [make_op()])
    assert f["rule"] == "MISSING_OPERATION" and f["requirement_id"] == "REQ-004"
    assert f["actual"] == "Missing"


def test_v05_required_field_mismatch_reports_extra_field_as_context():
    req = make_req(required_fields=[{"name": "note", "location": "body", "type": "string"}])
    op = make_op(required_fields=[{"name": "comment", "location": "body", "type": "string"}])
    [f] = validate_documents([req], [op])
    assert f["rule"] == "REQUIRED_FIELD_MISMATCH"
    assert f["expected"] == "note:body:string"
    assert "comment:body:string" in f["context"]


def test_v05_wrong_location_is_a_field_mismatch():
    req = make_req(required_fields=[{"name": "orderId", "location": "path", "type": "string"}])
    op = make_op(required_fields=[{"name": "orderId", "location": "query", "type": "string"}])
    assert rules(validate_documents([req], [op])) == ["REQUIRED_FIELD_MISMATCH"]


def test_v06_field_type_mismatch():
    req = make_req(required_fields=[{"name": "reason", "location": "body", "type": "string"}])
    op = make_op(required_fields=[{"name": "reason", "location": "body", "type": "integer"}])
    [f] = validate_documents([req], [op])
    assert (f["rule"], f["expected"], f["actual"]) == (
        "FIELD_TYPE_MISMATCH", "reason:body:string", "reason:body:integer")


def test_several_errors_in_one_operation_are_all_reported():
    op = make_op(path="/api/order", method="PUT", responses=["200"])
    assert rules(validate_documents([make_req()], [op])) == [
        "ENDPOINT_MISMATCH", "METHOD_MISMATCH", "STATUS_CODE_MISMATCH"]


def test_finding_ids_are_sequential():
    reqs = [make_req(requirement_id="REQ-001"), make_req(requirement_id="REQ-002")]
    findings = validate_documents(reqs, [])
    assert [f["finding_id"] for f in findings] == ["F001", "F002"]


def test_inputs_are_not_modified():
    reqs, ops = [make_req()], [make_op(path="/x")]
    before = copy.deepcopy((reqs, ops))
    validate_documents(reqs, ops)
    assert (reqs, ops) == before


# ---------- Sample datasets ----------

def test_baseline_has_zero_findings(baseline):
    assert validate_documents(*baseline) == []


def test_faulty_detects_exactly_the_planted_errors(faulty, ground_truth):
    score = score_against_ground_truth(validate_documents(*faulty), ground_truth)
    assert score["correctly_detected"] == 6
    assert score["false_positives"] == [] and score["missed_errors"] == []
    assert score["precision"] == 1.0 and score["recall"] == 1.0

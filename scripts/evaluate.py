"""Measure SpecAlign AI and write reports/evaluation.md.

    python scripts/evaluate.py            # validation + RAG (needs Ollama running)
    python scripts/evaluate.py --no-rag   # validation only

Validation results are scored automatically against ground_truth.json.
For RAG, retrieval relevance is checked automatically against the expected sources.
Whether each answer is fully supported by its sources is a human judgment, so the report
leaves a column for you to fill in after reading the answers.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.parsers import parse_openapi, parse_requirements  # noqa: E402
from src.validator import score_against_ground_truth, validate_documents  # noqa: E402

SAMPLES = ROOT / "sample_docs"
REPORT = ROOT / "reports" / "evaluation.md"

RAG_QUESTIONS = [
    {"question": "What does REQ-001 specify?",
     "expected": "POST /api/orders returning 201, with required body fields customerId (string) "
                 "and items (array).",
     "expected_sources": ["requirements.docx — REQ-001"]},
    {"question": "Which endpoint retrieves an order?",
     "expected": "The SRS (REQ-002) specifies GET /api/orders/{orderId}; the faulty API defines it "
                 "as POST (finding on REQ-002).",
     "expected_sources": ["requirements.docx — REQ-002", "openapi.yaml — getOrder"]},
    {"question": "Is the API consistent with REQ-001?",
     "expected": "No. REQ-001 specifies POST /api/orders; openapi.yaml createOrder uses POST /api/order.",
     "expected_sources": ["requirements.docx — REQ-001", "openapi.yaml — createOrder"]},
    {"question": "Which requirement has no corresponding API operation?",
     "expected": "REQ-004 (Update Order Status, PATCH /api/orders/{orderId}/status).",
     "expected_sources": ["requirements.docx — REQ-004"], "expected_finding_rule": "MISSING_OPERATION"},
    {"question": "What payment-processing API does the system support?",
     "expected": "The documents do not describe any payment-processing API; information is insufficient.",
     "expected_sources": []},
]


def load(dataset: str):
    reqs = parse_requirements(SAMPLES / dataset / "requirements.docx")
    ops = parse_openapi(SAMPLES / dataset / "openapi.yaml")
    return reqs, ops


def validation_section() -> list[str]:
    truth = json.loads((ROOT / "ground_truth.json").read_text(encoding="utf-8"))
    b_reqs, b_ops = load("baseline")
    b_findings = validate_documents(b_reqs, b_ops)
    f_reqs, f_ops = load("faulty")
    f_findings = validate_documents(f_reqs, f_ops)
    score = score_against_ground_truth(f_findings, truth)

    lines = [
        "## Validation results",
        "",
        "| Dataset | Requirements | API operations | Findings |",
        "| --- | --- | --- | --- |",
        f"| Baseline | {len(b_reqs)} | {len(b_ops)} | {len(b_findings)} |",
        f"| Faulty | {len(f_reqs)} | {len(f_ops)} | {len(f_findings)} |",
        "",
        "Faulty dataset scored against `ground_truth.json` (a finding counts as correct when both "
        "its requirement ID and its rule match a known error):",
        "",
        f"- Known errors: {score['known_errors']}",
        f"- Correctly detected: {score['correctly_detected']}",
        f"- False positives: {len(score['false_positives'])} {score['false_positives'] or ''}",
        f"- Missed errors: {len(score['missed_errors'])} {score['missed_errors'] or ''}",
        f"- Precision: {score['precision']:.2f}",
        f"- Recall: {score['recall']:.2f}",
        "",
        "| Finding | Requirement | Rule | Expected | Actual |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {f['finding_id']} | {f['requirement_id']} | {f['rule']} | `{f['expected']}` | "
              f"`{f['actual']}` |" for f in f_findings]
    print(f"Baseline: {len(b_findings)} findings. Faulty: {score['correctly_detected']}/"
          f"{score['known_errors']} detected, {len(score['false_positives'])} false positives, "
          f"precision {score['precision']:.2f}, recall {score['recall']:.2f}")
    return lines


def rag_section() -> list[str]:
    from src.rag import DocumentIndex, OllamaBackend, RAGAssistant, build_chunks

    backend = OllamaBackend()
    ok, message = backend.check()
    if not ok:
        print(f"RAG evaluation skipped: {message}")
        return ["## RAG evaluation", "", f"Not run: {message}"]

    reqs, ops = load("faulty")
    findings = validate_documents(reqs, ops)
    assistant = RAGAssistant(DocumentIndex(build_chunks(reqs, ops), backend), findings, backend)
    assistant.ask("warm-up question to load the model")  # first call loads the model into memory

    results, rows = [], []
    for item in RAG_QUESTIONS:
        print(f"Asking: {item['question']}")
        result = assistant.ask(item["question"])
        used_rules = {f["rule"] for f in findings if f["finding_id"] in result["findings_used"]}
        if item["expected_sources"]:
            relevant = all(s in result["sources"] for s in item["expected_sources"])
            if item.get("expected_finding_rule"):
                relevant = relevant or item["expected_finding_rule"] in used_rules
            relevant_text = "Yes" if relevant else "No"
        else:
            relevant_text = "N/A (out of scope)"
        results.append({**item, "retrieved": result["sources"], "findings_used": result["findings_used"],
                        "answer": result["answer"], "seconds": result["seconds"],
                        "retrieval_relevant": relevant_text})
        rows.append(result["seconds"])

    (ROOT / "reports" / "rag_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    lines = ["## RAG evaluation (faulty dataset)", "",
             f"Machine: {platform.system()} {platform.release()}, {platform.processor() or 'CPU'} "
             "[add RAM / GPU]. Models: qwen3:4b, nomic-embed-text.",
             f"Average answer time: {sum(rows) / len(rows):.1f} s "
             f"(min {min(rows):.1f} s, max {max(rows):.1f} s), after one warm-up call.", "",
             "| # | Question | Retrieval relevant | Answer supported by sources | Time (s) |",
             "| --- | --- | --- | --- | --- |"]
    for n, r in enumerate(results, 1):
        lines.append(f"| {n} | {r['question']} | {r['retrieval_relevant']} | [review: Yes/Partly/No] | "
                     f"{r['seconds']} |")
    lines += ["", "Fill in the \"Answer supported\" column by reading each answer below against its "
              "retrieved evidence.", ""]
    for n, r in enumerate(results, 1):
        lines += [f"### Q{n}. {r['question']}", "",
                  f"**Expected:** {r['expected']}", "",
                  f"**Retrieved:** {'; '.join(r['retrieved'])}", "",
                  f"**Findings used:** {', '.join(r['findings_used']) or 'none'}", "",
                  "**Generated answer:**", "", "> " + r["answer"].replace("\n", "\n> "), ""]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-rag", action="store_true", help="skip the Ollama-based RAG evaluation")
    args = parser.parse_args()

    lines = ["# SpecAlign AI evaluation", "", f"Generated {date.today().isoformat()} by "
             "`scripts/evaluate.py`.", ""]
    lines += validation_section() + [""]
    lines += (["## RAG evaluation", "", "Not run (`--no-rag`)."] if args.no_rag else rag_section())
    lines += ["", "## Limitations", "",
              "- Supports only DOCX requirements tables and OpenAPI YAML.",
              "- Assumes a predefined SRS table structure and `fieldName:location:type` notation.",
              "- Supports simple inline request schemas with top-level required fields.",
              "- Requires explicit `x-requirement-id` mappings.",
              "- The dataset is small and synthetic; results show the approach works, not "
              "accuracy on real documents.",
              "- AI-generated explanations require human review."]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

"""Tests for chunking, retrieval, and prompt assembly.

These use a fake backend (word-hashing embeddings and a recording "LLM"), so they run
without Ollama. They check the RAG plumbing, not the quality of Qwen3's answers;
answer quality is measured separately by scripts/evaluate.py.
"""
import re

import numpy as np
import pytest

from src.rag import (DocumentIndex, RAGAssistant, SYSTEM_PROMPT, build_chunks,
                     mentioned_requirement_ids, strip_thinking)
from src.validator import validate_documents


class FakeBackend:
    """Bag-of-words embeddings: texts sharing words get similar vectors."""

    dims = 256

    def __init__(self):
        self.prompts = []

    def embed(self, texts):
        out = np.zeros((len(texts), self.dims), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"[a-z]+", text.lower()):
                out[row, hash(word) % self.dims] += 1.0
        return out

    def generate(self, system, prompt):
        self.prompts.append((system, prompt))
        return "fake answer"


@pytest.fixture
def faulty_assistant(faulty):
    reqs, ops = faulty
    backend = FakeBackend()
    index = DocumentIndex(build_chunks(reqs, ops), backend)
    return RAGAssistant(index, validate_documents(reqs, ops), backend), backend


def test_faulty_dataset_has_19_chunks(faulty):
    chunks = build_chunks(*faulty)
    assert len(chunks) == 19
    assert sum(c["kind"] == "requirement" for c in chunks) == 10
    assert sum(c["kind"] == "operation" for c in chunks) == 9


def test_chunks_keep_source_metadata(faulty):
    chunks = build_chunks(*faulty)
    srs = next(c for c in chunks if c["kind"] == "requirement" and c["requirement_id"] == "REQ-001")
    api = next(c for c in chunks if c["kind"] == "operation" and c["requirement_id"] == "REQ-001")
    assert srs["label"] == "requirements.docx — REQ-001"
    assert api["label"] == "openapi.yaml — createOrder"
    assert "Path: /api/order\n" in api["text"]


@pytest.mark.parametrize("question,expected", [
    ("What does REQ-001 specify?", ["REQ-001"]),
    ("compare req 7 and REQ-10", ["REQ-007", "REQ-010"]),
    ("Which endpoint retrieves an order?", []),
])
def test_mentioned_requirement_ids(question, expected):
    assert mentioned_requirement_ids(question) == expected


def test_named_requirement_retrieves_both_documents(faulty_assistant):
    assistant, _ = faulty_assistant
    chunks = assistant.index.retrieve("Is the API consistent with REQ-001?", k=4)
    assert len(chunks) == 4
    top_two = {(c["kind"], c["requirement_id"]) for c in chunks[:2]}
    assert top_two == {("requirement", "REQ-001"), ("operation", "REQ-001")}


def test_named_operation_id_pulls_in_its_requirement(faulty_assistant):
    assistant, _ = faulty_assistant
    chunks = assistant.index.retrieve("What does createOrder require?", k=4)
    assert {c["requirement_id"] for c in chunks[:2]} == {"REQ-001"}


def test_missing_operation_question_uses_complete_findings(faulty_assistant):
    assistant, backend = faulty_assistant
    result = assistant.ask("Which requirement has no corresponding API operation?")
    assert len(result["findings_used"]) == 6
    assert "MISSING_OPERATION" in backend.prompts[-1][1]


def test_specific_question_uses_only_relevant_findings(faulty_assistant):
    assistant, _ = faulty_assistant
    result = assistant.ask("What does REQ-001 specify?")
    assert result["findings_used"] == ["F001"]


def test_unnamed_question_only_uses_findings_for_retrieved_chunks(faulty_assistant):
    assistant, _ = faulty_assistant
    result = assistant.ask("What payment-processing API does the system support?")
    retrieved = {c["requirement_id"] for c in result["chunks"]}
    used = {f["requirement_id"] for f in assistant.findings if f["finding_id"] in result["findings_used"]}
    assert used <= retrieved


def test_prompt_says_when_no_findings_apply(baseline):
    backend = FakeBackend()
    assistant = RAGAssistant(DocumentIndex(build_chunks(*baseline), backend), [], backend)
    assistant.ask("Which endpoint retrieves an order?")
    assert "No validation findings apply" in backend.prompts[-1][1]


def test_prompt_uses_system_prompt_and_asks_for_sources(faulty_assistant):
    assistant, backend = faulty_assistant
    assistant.ask("Which endpoint retrieves an order?")
    system, prompt = backend.prompts[-1]
    assert system == SYSTEM_PROMPT
    assert "Sources:" in prompt and "Document excerpts:" in prompt


def test_explain_finding_uses_both_chunks_and_the_finding(faulty_assistant):
    assistant, _ = faulty_assistant
    finding = assistant.findings[0]
    result = assistant.explain_finding(finding)
    assert result["sources"] == ["requirements.docx — REQ-001", "openapi.yaml — createOrder"]
    assert result["findings_used"] == [finding["finding_id"]]


def test_explain_missing_operation_has_only_srs_evidence(faulty_assistant):
    assistant, _ = faulty_assistant
    finding = next(f for f in assistant.findings if f["rule"] == "MISSING_OPERATION")
    result = assistant.explain_finding(finding)
    assert result["sources"] == ["requirements.docx — REQ-004"]


def test_strip_thinking():
    assert strip_thinking("<think>\nreasoning\n</think>\n\nAnswer.") == "Answer."

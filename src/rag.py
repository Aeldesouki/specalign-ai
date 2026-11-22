"""Local retrieval-augmented generation (RAG) over the parsed documents.

Pipeline: question -> embed -> cosine search over document chunks -> Qwen3:4B -> cited answer.

Everything runs locally through Ollama:
    nomic-embed-text  turns text into vectors for retrieval
    qwen3:4b          writes answers and explanations

The model never decides whether documents match. It receives the deterministic findings
from validator.py and explains them using only the retrieved excerpts.

The backend is passed in as an object with `embed(texts)` and `generate(system, prompt)`,
so tests can swap in a fake backend and run without Ollama.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

CHAT_MODEL = "qwen3:4b"
EMBED_MODEL = "nomic-embed-text"
TOP_K = 4

SYSTEM_PROMPT = """You are a software engineering documentation assistant.

Answer questions using only the provided document excerpts and validation findings.

Do not invent API endpoints, methods, fields, or response codes.

Cite the relevant document and requirement ID in your answer.

When documents disagree, describe both values.

Explain the possible engineering implications of identified discrepancies.

Do not automatically decide which document is correct.

If the available evidence does not support an answer, state that the information is insufficient."""

REQ_ID_PATTERN = re.compile(r"\bREQ-?\s?(\d{1,3})\b", re.IGNORECASE)
# Questions about what is missing or wrong overall need the complete validation results,
# because a missing operation has no chunk that similarity search could find.
WHOLE_RESULT_PATTERN = re.compile(
    r"\b(missing|no corresponding|not implemented|without (an? )?(api )?operation|"
    r"no (api )?operation|discrepanc\w*|inconsisten\w*|mismatch\w*|errors?|issues?|problems?)\b",
    re.IGNORECASE)

# nomic-embed-text is trained with these task prefixes for retrieval.
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class Backend(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...
    def generate(self, system: str, prompt: str) -> str: ...


class OllamaUnavailable(RuntimeError):
    """Raised when Ollama is not running or a required model has not been pulled."""


class OllamaBackend:
    """Calls the local Ollama server for embeddings and chat."""

    def __init__(self, chat_model: str = CHAT_MODEL, embed_model: str = EMBED_MODEL,
                 host: str | None = None):
        import ollama  # imported here so validation works even if the package is missing

        self.chat_model = chat_model
        self.embed_model = embed_model
        self.client = ollama.Client(host=host) if host else ollama.Client()

    def check(self) -> tuple[bool, str]:
        """Return (ok, message) describing whether Ollama and both models are ready."""
        try:
            listed = self.client.list()
        except Exception as err:  # connection refused, timeout, etc.
            return False, ("Ollama is not reachable. Install it from https://ollama.com and "
                           f"make sure it is running. ({err.__class__.__name__})")
        names = [m["model"] for m in listed["models"]]
        missing = [m for m in (self.chat_model, self.embed_model)
                   if not any(n == m or n.startswith(m + ":") for n in names)]
        if missing:
            pulls = " and ".join(f"`ollama pull {m}`" for m in missing)
            return False, f"Ollama is running but missing model(s): {', '.join(missing)}. Run {pulls}."
        return True, f"Ollama ready ({self.chat_model}, {self.embed_model})."

    def embed(self, texts: list[str]) -> np.ndarray:
        try:
            response = self.client.embed(model=self.embed_model, input=texts)
        except Exception as err:
            raise OllamaUnavailable(f"Embedding failed: {err}") from err
        return np.asarray(response["embeddings"], dtype=np.float32)

    def generate(self, system: str, prompt: str) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        try:
            try:
                # think=False asks Qwen3 to skip its hidden reasoning, which is much faster.
                response = self.client.chat(model=self.chat_model, messages=messages,
                                            think=False, options={"temperature": 0})
            except TypeError:  # older ollama package without the `think` argument
                response = self.client.chat(model=self.chat_model, messages=messages,
                                            options={"temperature": 0})
        except Exception as err:
            raise OllamaUnavailable(f"Generation failed: {err}") from err
        return strip_thinking(response["message"]["content"])


def strip_thinking(text: str) -> str:
    """Remove any <think>...</think> block Qwen3 may emit before its answer."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ---------- Chunking ----------

def _fields_text(fields: list[dict]) -> str:
    if not fields:
        return "Required fields: none"
    lines = [f"- {f['name']}, {f['location']}, {f['type']}" for f in fields]
    return "Required fields:\n" + "\n".join(lines)


def requirement_chunk(req: dict) -> dict[str, Any]:
    text = "\n".join([
        f"Source: {req['source']}",
        f"Requirement: {req['requirement_id']}",
        f"Title: {req.get('title', '')}",
        f"Description: {req.get('description', '')}",
        f"Method: {req['method']}",
        f"Path: {req['path']}",
        f"Expected status: {req['success_status']}",
        _fields_text(req["required_fields"]),
    ])
    return {"text": text, "source": req["source"], "kind": "requirement",
            "requirement_id": req["requirement_id"], "operation_id": None,
            "label": f"{req['source']} — {req['requirement_id']}"}


def operation_chunk(op: dict) -> dict[str, Any]:
    text = "\n".join([
        f"Source: {op['source']}",
        f"Requirement: {op.get('requirement_id') or 'not linked'}",
        f"Operation: {op['operation_id']}",
        f"Summary: {op.get('summary', '')}",
        f"Method: {op['method']}",
        f"Path: {op['path']}",
        f"Response: {', '.join(op['responses'])}",
        _fields_text(op["required_fields"]),
    ])
    return {"text": text, "source": op["source"], "kind": "operation",
            "requirement_id": op.get("requirement_id"), "operation_id": op["operation_id"],
            "label": f"{op['source']} — {op['operation_id']}"}


def build_chunks(requirements: list[dict], operations: list[dict]) -> list[dict[str, Any]]:
    """One chunk per SRS requirement and one per API operation."""
    return [requirement_chunk(r) for r in requirements] + [operation_chunk(o) for o in operations]


# ---------- Retrieval ----------

def _normalise_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def mentioned_requirement_ids(question: str) -> list[str]:
    """Find requirement IDs such as 'REQ-001', 'req 1' or 'REQ-01' in a question."""
    return [f"REQ-{int(n):03d}" for n in REQ_ID_PATTERN.findall(question)]


@dataclass
class DocumentIndex:
    """Chunks plus their unit-length embedding vectors, held in a NumPy array."""

    chunks: list[dict[str, Any]]
    backend: Backend
    vectors: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        texts = [DOC_PREFIX + c["text"] for c in self.chunks]
        self.vectors = _normalise_rows(np.asarray(self.backend.embed(texts), dtype=np.float32))

    def chunks_for_requirement(self, requirement_id: str) -> list[dict[str, Any]]:
        return [c for c in self.chunks if c["requirement_id"] == requirement_id]

    def retrieve(self, question: str, k: int = TOP_K) -> list[dict[str, Any]]:
        """Return up to k chunks (more if a question names several requirements).

        Chunks for any requirement ID named in the question are always included first,
        so both sides of a comparison are retrieved. Remaining slots are filled by
        cosine similarity.
        """
        query = _normalise_rows(np.asarray(self.backend.embed([QUERY_PREFIX + question]),
                                           dtype=np.float32))[0]
        scores = self.vectors @ query

        chosen: list[int] = []
        named_ops = [i for i, c in enumerate(self.chunks)
                     if c["operation_id"] and re.search(rf"\b{re.escape(c['operation_id'])}\b",
                                                        question, re.IGNORECASE)]
        named_ids = mentioned_requirement_ids(question)
        named_ids += [self.chunks[i]["requirement_id"] for i in named_ops
                      if self.chunks[i]["requirement_id"]]
        for req_id in dict.fromkeys(named_ids):
            for i, chunk in enumerate(self.chunks):
                if chunk["requirement_id"] == req_id and i not in chosen:
                    chosen.append(i)

        for i in np.argsort(-scores):
            if len(chosen) >= k:
                break
            if int(i) not in chosen:
                chosen.append(int(i))

        results = []
        for i in chosen:
            chunk = dict(self.chunks[i])
            chunk["score"] = float(scores[i])
            results.append(chunk)
        return results


# ---------- Answering ----------

def _format_finding(f: dict) -> str:
    line = (f"- {f['finding_id']} {f['requirement_id']} {f['rule']}: expected {f['expected']}, "
            f"actual {f['actual']}. Sources: {', '.join(f['sources'])}.")
    if f.get("context"):
        line += f" Context: {f['context']}"
    return line


def build_prompt(question: str, chunks: list[dict], findings: list[dict]) -> str:
    excerpts = "\n\n".join(f"[{n}] {c['label']}\n{c['text']}" for n, c in enumerate(chunks, 1))
    finding_text = ("\n".join(_format_finding(f) for f in findings)
                    if findings else "No validation findings apply to these excerpts.")
    return (f"Question: {question}\n\n"
            f"Document excerpts:\n{excerpts}\n\n"
            f"Deterministic validation findings:\n{finding_text}\n\n"
            "Answer using only the excerpts and findings above. End with a line starting "
            "'Sources:' that lists the documents and requirement IDs or operation IDs you used.")


class RAGAssistant:
    """Answers questions and explains findings using retrieved evidence."""

    def __init__(self, index: DocumentIndex, findings: list[dict], backend: Backend, k: int = TOP_K):
        self.index = index
        self.findings = findings
        self.backend = backend
        self.k = k

    def relevant_findings(self, question: str, chunks: list[dict]) -> list[dict]:
        """Pick the deterministic findings to show the model.

        - Question names requirement IDs: only those requirements' findings.
        - Question asks about missing items or errors overall: all findings.
        - Otherwise: findings for the requirements that were retrieved.
        """
        named = set(mentioned_requirement_ids(question))
        if named:
            return [f for f in self.findings if f["requirement_id"] in named]
        if WHOLE_RESULT_PATTERN.search(question):
            return list(self.findings)
        retrieved = {c["requirement_id"] for c in chunks}
        return [f for f in self.findings if f["requirement_id"] in retrieved]

    def _run(self, question: str, chunks: list[dict], findings: list[dict]) -> dict[str, Any]:
        start = time.perf_counter()
        answer = self.backend.generate(SYSTEM_PROMPT, build_prompt(question, chunks, findings))
        return {
            "question": question,
            "answer": answer,
            "chunks": chunks,
            "sources": [c["label"] for c in chunks],
            "findings_used": [f["finding_id"] for f in findings],
            "seconds": round(time.perf_counter() - start, 2),
        }

    def ask(self, question: str) -> dict[str, Any]:
        start = time.perf_counter()
        chunks = self.index.retrieve(question, self.k)
        result = self._run(question, chunks, self.relevant_findings(question, chunks))
        result["seconds"] = round(time.perf_counter() - start, 2)
        return result

    def explain_finding(self, finding: dict) -> dict[str, Any]:
        """Explain one validator finding using both documents' chunks for that requirement."""
        chunks = [dict(c, score=1.0) for c in self.index.chunks_for_requirement(finding["requirement_id"])]
        question = (f"Explain validation finding {finding['finding_id']} "
                    f"({finding['rule']}) for {finding['requirement_id']}: what does each document "
                    "say, why is this an inconsistency, and what could it affect in development or "
                    "integration? Recommend what an engineer should confirm.")
        return self._run(question, chunks, [finding])

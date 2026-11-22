"""SpecAlign AI: Streamlit interface.

Run with:
    streamlit run app.py
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import streamlit as st

from src.parsers import ParseError, parse_openapi, parse_requirements
from src.rag import DocumentIndex, OllamaBackend, OllamaUnavailable, RAGAssistant, build_chunks
from src.validator import RULE_LABELS, findings_to_json, validate_documents

SAMPLES = Path(__file__).parent / "sample_docs"

st.set_page_config(page_title="SpecAlign AI", page_icon="🔎", layout="wide")


# ---------- Helpers ----------

@st.cache_resource(show_spinner=False)
def get_backend() -> OllamaBackend | None:
    try:
        return OllamaBackend()
    except ImportError:
        return None


def ollama_status() -> tuple[bool, str]:
    backend = get_backend()
    if backend is None:
        return False, "The `ollama` Python package is not installed (`pip install ollama`)."
    return backend.check()


@st.cache_resource(show_spinner="Embedding document chunks with nomic-embed-text...")
def get_index(docs_key: str, _chunks: list[dict]) -> DocumentIndex:
    """Build the vector index once per pair of documents (keyed by their content hash)."""
    return DocumentIndex(_chunks, get_backend())


def load_documents(srs_name: str, srs_bytes: bytes, api_name: str, api_bytes: bytes) -> None:
    """Parse both files, run validation, and store everything in session state."""
    requirements = parse_requirements(io.BytesIO(srs_bytes), source_name=srs_name)
    operations = parse_openapi(io.BytesIO(api_bytes), source_name=api_name)
    st.session_state.update(
        requirements=requirements,
        operations=operations,
        findings=validate_documents(requirements, operations),
        docs_key=hashlib.sha256(srs_bytes + b"\0" + api_bytes).hexdigest(),
        doc_names=(srs_name, api_name),
        explanation=None,
        last_answer=None,
    )


def get_assistant() -> RAGAssistant:
    chunks = build_chunks(st.session_state.requirements, st.session_state.operations)
    index = get_index(st.session_state.docs_key, chunks)
    return RAGAssistant(index, st.session_state.findings, get_backend())


def show_answer(result: dict) -> None:
    st.markdown(result["answer"])
    st.caption(f"Answered in {result['seconds']} s · Findings used: "
               f"{', '.join(result['findings_used']) or 'none'}")
    with st.expander(f"Retrieved evidence ({len(result['chunks'])} chunks)", expanded=False):
        for chunk in result["chunks"]:
            st.markdown(f"**{chunk['label']}** · similarity {chunk['score']:.3f}")
            st.code(chunk["text"], language="text")


# ---------- Sidebar ----------

with st.sidebar:
    st.header("SpecAlign AI")
    st.write("Checks a Word SRS against an OpenAPI spec, then explains the differences "
             "with a local model.")
    ok, message = ollama_status()
    (st.success if ok else st.warning)(message)
    if not ok:
        st.caption("Validation still works without Ollama. Only explanations and Q&A need it.")
    st.divider()
    st.caption("All processing is local. Models: qwen3:4b and nomic-embed-text via Ollama.")

st.title("SpecAlign AI")
st.caption("RAG-enhanced software documentation validator")

validate_tab, ask_tab = st.tabs(["1 · Validate Documents", "2 · Ask the Documentation"])

# ---------- Tab 1: Validate ----------

with validate_tab:
    source = st.radio("Documents", ["Upload my own", "Sample: baseline (consistent)",
                                    "Sample: faulty (6 planted errors)"], horizontal=True)
    if source == "Upload my own":
        col1, col2 = st.columns(2)
        srs_file = col1.file_uploader("Software Requirements Specification (.docx)", type=["docx"])
        api_file = col2.file_uploader("OpenAPI specification (.yaml / .yml)", type=["yaml", "yml"])
    else:
        folder = SAMPLES / ("baseline" if "baseline" in source else "faulty")
        st.caption(f"Using `sample_docs/{folder.name}/requirements.docx` and "
                   f"`sample_docs/{folder.name}/openapi.yaml`.")

    if st.button("Run Validation", type="primary"):
        try:
            if source == "Upload my own":
                if not (srs_file and api_file):
                    st.error("Upload both a .docx SRS and an OpenAPI YAML file first.")
                    st.stop()
                load_documents(srs_file.name, srs_file.getvalue(), api_file.name, api_file.getvalue())
            else:
                load_documents("requirements.docx", (folder / "requirements.docx").read_bytes(),
                               "openapi.yaml", (folder / "openapi.yaml").read_bytes())
        except ParseError as err:
            st.error(f"Could not read the documents: {err}")
            st.stop()

    if "findings" in st.session_state:
        findings = st.session_state.findings
        srs_name, api_name = st.session_state.doc_names
        m1, m2, m3 = st.columns(3)
        m1.metric("Requirements", len(st.session_state.requirements))
        m2.metric("API operations", len(st.session_state.operations))
        m3.metric("Discrepancies", len(findings))

        if not findings:
            st.success(f"No discrepancies: {srs_name} and {api_name} are consistent.")
        else:
            st.dataframe(
                [{"Finding": f["finding_id"], "Requirement": f["requirement_id"],
                  "Issue": RULE_LABELS[f["rule"]], "Expected": f["expected"],
                  "Actual": f["actual"], "Sources": ", ".join(f["sources"])} for f in findings],
                hide_index=True, width="stretch")

            st.subheader("Explain a finding")
            choice = st.selectbox(
                "Finding", findings,
                format_func=lambda f: f"{f['finding_id']} · {f['requirement_id']} · {RULE_LABELS[f['rule']]}")
            if st.button("Generate AI explanation"):
                ok, message = ollama_status()
                if not ok:
                    st.warning(message)
                else:
                    try:
                        with st.spinner("Asking qwen3:4b..."):
                            st.session_state.explanation = get_assistant().explain_finding(choice)
                    except OllamaUnavailable as err:
                        st.warning(str(err))
            if st.session_state.get("explanation"):
                st.info("AI-generated explanation. Review it against the documents before acting.")
                show_answer(st.session_state.explanation)

        st.download_button("Download findings (JSON)", findings_to_json(findings),
                           file_name="specalign_findings.json", mime="application/json")

# ---------- Tab 2: Ask ----------

with ask_tab:
    if "findings" not in st.session_state:
        st.info("Run validation in tab 1 first so there are documents to search.")
    else:
        st.caption("Examples: What does REQ-001 specify? · Which endpoint retrieves an order? · "
                   "Is the API consistent with REQ-001? · Which requirement has no corresponding "
                   "API operation?")
        question = st.text_input("Question about the documents")
        if st.button("Ask", type="primary") and question.strip():
            ok, message = ollama_status()
            if not ok:
                st.warning(message)
            else:
                try:
                    with st.spinner("Retrieving evidence and asking qwen3:4b..."):
                        st.session_state.last_answer = get_assistant().ask(question.strip())
                except OllamaUnavailable as err:
                    st.warning(str(err))
        if st.session_state.get("last_answer"):
            st.info("AI-generated answer. Check the retrieved evidence below.")
            show_answer(st.session_state.last_answer)

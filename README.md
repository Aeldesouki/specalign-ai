# SpecAlign AI

**A RAG-enhanced validator that checks whether a software requirements specification and an
OpenAPI specification agree, and explains the differences using a local language model.**

SpecAlign AI reads a Word SRS and an OpenAPI 3.0 YAML file and finds inconsistencies with
deterministic Python rules, such as a wrong endpoint, method, status code, required field, or field
type. A local Qwen3:4B model, given only retrieved passages from the two documents, explains each
finding and answers questions with citations. Everything runs on your own machine through Ollama.

![SpecAlign AI dashboard](screenshots/dashboard.png)

---

## 1. Problem statement

Teams describe one system in several documents. The SRS says what the system must do. The OpenAPI
spec says exactly how clients call it. When the two drift apart (a renamed path, a changed status
code, a field typed as an integer instead of a string), the mismatch usually surfaces late, during
integration or testing, when it is expensive to fix.

Checking the documents by hand means matching every requirement to its operation and comparing each
path, method, status code, and field. That review is slow and repetitive, and it is easy to miss
something. SpecAlign AI automates the comparison and helps a reviewer understand what it finds.

## 2. Solution overview

SpecAlign AI combines three parts:

1. **Document parsers** turn the Word table and the YAML file into the same record format.
2. **A rule-based validator** compares the records with six exact checks. It decides whether the
   documents match.
3. **Local RAG** (retrieval-augmented generation) retrieves the relevant passages from both
   documents and asks Qwen3:4B to explain a finding or answer a question, citing its sources.

The language model never decides whether two values match; that is the validator's job. The model
only explains evidence the rules have already established.

## 3. Architecture

```
          Word SRS + OpenAPI YAML
                     |
                     v
             Document Parsers            src/parsers.py
                     |
             +-------+--------+
             |                |
             v                v
         Validator        RAG Index        src/validator.py  |  src/rag.py
      (6 exact rules)  (nomic-embed-text,
             |          NumPy cosine)
             v                |
          Findings        Retrieval
             |                |
             +-------+--------+
                     |
                     v
             Local Qwen3:4B              via Ollama
                     |
                     v
           Streamlit Interface           app.py
```

Validation results go straight to the interface too, so validation still works when Ollama is off.

### Validation rules

| Rule | Name | Example |
| --- | --- | --- |
| V01 | `ENDPOINT_MISMATCH` | `/api/orders` vs `/api/order` |
| V02 | `METHOD_MISMATCH` | `GET` vs `POST` |
| V03 | `STATUS_CODE_MISMATCH` | expected `200` is not defined; only `204` is |
| V04 | `MISSING_OPERATION` | no operation has `x-requirement-id: REQ-004` |
| V05 | `REQUIRED_FIELD_MISMATCH` | required `note` missing; API requires `comment` instead |
| V06 | `FIELD_TYPE_MISMATCH` | `reason` is `string` vs `integer` |

V03 only checks that the expected success code exists, so additional error responses such as 400 or
404 are not reported.

### Design decisions

- **Why deterministic validation instead of asking an LLM to find discrepancies?** Comparing `200`
  with `204` is an exact check. Python gives the same answer every time and can be unit tested. An
  LLM can miss a difference, invent one, or answer differently on the next run.
- **Why RAG?** The model has never seen these documents. Retrieval puts the relevant excerpts in
  front of it, so its explanation is based on the actual text and can cite it.
- **How are requirements matched to operations?** By the `x-requirement-id` property on each
  OpenAPI operation, not by path. Because the path is one of the values that might be wrong, matching
  on it would turn a typo into a "missing operation".
- **How are unsupported answers avoided?** The system prompt limits the model to the supplied
  excerpts and findings, requires citations, and tells it to say the information is insufficient
  when the evidence does not answer the question. If a question names a requirement ID, both
  documents' chunks for that ID are always retrieved. Questions about what is missing use the full
  validation results, because a missing operation has no chunk for search to find.
- **Why a small local model?** Engineering documents are often confidential, so no text leaves the
  machine. A small 4B model runs on a laptop, and its job (explaining given evidence) does not need a
  large model.

## 4. Installation

Requirements: Python 3.11 or 3.12, and [Ollama](https://ollama.com).

**Windows (Command Prompt):**

```bat
git clone https://github.com/<your-username>/specalign-ai.git
cd specalign-ai
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

**macOS / Linux:**

```bash
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
```

**Local models** (after installing Ollama, which starts in the background):

```bash
ollama pull qwen3:4b
ollama pull nomic-embed-text
```

## 5. How to run

```bash
streamlit run app.py
```

Your browser opens the app.

**Tab 1: Validate Documents**

1. Choose **Sample: baseline** or **Sample: faulty**, or upload your own `.docx` and `.yaml`.
2. Click **Run Validation** to see counts and the discrepancy table.
3. Select a finding and click **Generate AI explanation**.
4. Click **Download findings (JSON)** to export the results.

**Tab 2: Ask the Documentation:** type a question such as *"Is the API consistent with REQ-001?"*
and click **Ask**. The answer appears with the retrieved evidence and similarity scores.

**Tests and evaluation:**

```bash
python -m pytest                  # unit tests (no Ollama needed)
python scripts/evaluate.py        # writes reports/evaluation.md (RAG part needs Ollama)
```

**Regenerating the sample data:**

```bash
python data_generation/build_srs.py     # baseline requirements.docx
python data_generation/make_faulty.py   # faulty copy + ground_truth.json
```

## 6. Evaluation results

Full report: [`reports/evaluation.md`](reports/evaluation.md).

### Validation (measured with `scripts/evaluate.py`)

| Dataset | Requirements | API operations | Findings |
| --- | --- | --- | --- |
| Baseline | 10 | 10 | 0 |
| Faulty (6 planted errors) | 10 | 9 | 6 |

On the faulty dataset, all 6 planted errors were detected, with 0 false positives and 0 missed
(precision 1.00, recall 1.00). A finding counts as correct only when both its requirement ID and its
rule match `ground_truth.json`. The test suite has 36 passing tests covering every rule, the
matching logic, the parsers, and the RAG retrieval and prompt-building logic.

### RAG (5 test questions, faulty dataset)

<!-- Fill this in from reports/evaluation.md after running scripts/evaluate.py with Ollama. -->

| Measure | Result |
| --- | --- |
| Retrieval relevant | [X] of 4 in-scope questions |
| Answers fully supported by sources | [X] of 5 |
| Out-of-scope question (payment API) declined | [Yes/No] |
| Average answer time | [X] s on [your CPU / RAM / GPU] |

## 7. Limitations

- Supports only DOCX (requirements table) and OpenAPI YAML.
- Assumes a predefined SRS table structure and the `fieldName:location:type` notation.
- Supports simple inline request schemas with top-level required fields; nested schemas, multiple
  content types, and `$ref` resolution are out of scope.
- Requires explicit `x-requirement-id` mappings between documents.
- The dataset is small and synthetic, so the results show the approach works but not its accuracy
  on real documents.
- AI-generated explanations require human review.

**Possible improvements:** Excel, PowerPoint, and PDF inputs; `$ref` and nested schema support;
fuzzy matching when IDs are missing; a larger test set with more error types; and a reviewer workflow
where accepted or rejected findings become new test cases.

## 8. Synthetic data disclosure

All documents in `sample_docs/` are fictional and describe an imaginary Online Order Management
System. They were generated with Claude for this project. The prompts and process are in
[`data_generation/claude_prompts.md`](data_generation/claude_prompts.md). The six errors in the faulty
dataset were introduced by a script, not by an LLM, so that nothing else changed.

The application itself uses only local models through Ollama. No OpenAI models, APIs, SDKs, or
services are used, and no API keys are needed. Do not upload confidential documents to public
repositories.

## Project structure

```
specalign-ai/
├── app.py                      Streamlit interface
├── requirements.txt
├── src/
│   ├── parsers.py              Word SRS and OpenAPI parsers
│   ├── validator.py            Six deterministic rules + scoring
│   └── rag.py                  Chunking, embeddings, retrieval, Qwen3 answers
├── sample_docs/
│   ├── baseline/               Consistent SRS + API spec
│   └── faulty/                 Same SRS + API spec with 6 planted errors
├── data_generation/            Claude prompts and generation scripts
├── ground_truth.json           The 6 planted errors (evaluation only)
├── tests/                      pytest suite
├── scripts/evaluate.py         Writes reports/evaluation.md
├── reports/evaluation.md
└── screenshots/
```

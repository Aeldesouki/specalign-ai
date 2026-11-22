# Synthetic data generation

All documents in `sample_docs/` are **fictional**. They describe an imaginary Online Order
Management System and were generated with Claude for this portfolio project. They contain no
real or confidential information.

Claude was used **only to create the test documents**. The SpecAlign AI application itself runs
entirely on local models through Ollama (Qwen3:4B and nomic-embed-text). No OpenAI models or
external inference APIs are used at runtime.

## How the files were produced

| File | How it was made |
| --- | --- |
| `sample_docs/baseline/requirements.docx` | Requirements generated with Prompt 1, saved as a real Word table by `build_srs.py` |
| `sample_docs/baseline/openapi.yaml` | Generated with Prompt 2 from the SRS, then checked by hand and by the validator (0 findings) |
| `sample_docs/faulty/requirements.docx` | Unchanged copy of the baseline SRS |
| `sample_docs/faulty/openapi.yaml` | Six controlled edits made by `make_faulty.py` (not by an LLM) |
| `ground_truth.json` | Written by `make_faulty.py`; used only for evaluation, never given to the RAG system |

The faulty file is created by a script rather than by asking an LLM to rewrite the spec, so
that exactly six changes are made and nothing else drifts.

## Input table

| ID | Operation | HTTP Method | API Path | Success Status |
| --- | --- | --- | --- | --- |
| REQ-001 | Create Order | POST | /api/orders | 201 |
| REQ-002 | Retrieve Order | GET | /api/orders/{orderId} | 200 |
| REQ-003 | List Customer Orders | GET | /api/customers/{customerId}/orders | 200 |
| REQ-004 | Update Order Status | PATCH | /api/orders/{orderId}/status | 200 |
| REQ-005 | Add Order Note | POST | /api/orders/{orderId}/notes | 201 |
| REQ-006 | Create Return Request | POST | /api/returns | 201 |
| REQ-007 | Cancel Order | POST | /api/orders/{orderId}/cancel | 200 |
| REQ-008 | Retrieve Order Status | GET | /api/orders/{orderId}/status | 200 |
| REQ-009 | Update Delivery Address | PATCH | /api/orders/{orderId}/address | 200 |
| REQ-010 | Retrieve Return | GET | /api/returns/{returnId} | 200 |

## Prompt 1: Software Requirements Specification

```text
You are a senior software requirements engineer.

I am preparing fictional documentation for a software engineering portfolio project called
SpecAlign AI.

Create a professional Software Requirements Specification for an Online Order Management System.
Use the exact 10 requirement IDs, operations, HTTP methods, endpoint paths, and success response
codes provided in the table below.

[10-row requirements table above]

For each requirement, include:
1. Requirement ID
2. Requirement title
3. Functional description beginning with "The system shall..."
4. HTTP method
5. API endpoint path
6. Expected HTTP success status
7. Required request fields and their data types

Represent required fields using fieldName:location:type, separating multiple fields with
semicolons.

Include the necessary path parameters and realistic required body fields. In particular, REQ-005
must require note:body:string and REQ-006 must require reason:body:string.

Present the requirements in one consistently structured table, suitable for extraction using
Python's python-docx library.

Do not change the supplied endpoint paths or response codes.

Use only fictional information. Do not create an API implementation or backend code.
```

## Prompt 2: OpenAPI specification

```text
Act as a senior REST API engineer.

Generate a valid OpenAPI 3.0.3 YAML specification from the attached Software Requirements
Specification.

Requirements:
- Create exactly 10 API operations, one for each SRS requirement.
- Preserve all requirement IDs, endpoint paths, HTTP methods, and expected success response codes.
- Include all required path parameters and request-body fields.
- Match the SRS field names, data types, and required status.
- Add a unique operationId to every operation.
- Add an x-requirement-id property to every operation, containing the matching requirement ID.
- Use simple inline schemas rather than reusable $ref schemas.
- Include an info section with a title and version.
- Include realistic operation summaries and response descriptions.
- Do not add extra operations or change the SRS requirements.

Return valid YAML suitable for saving as openapi.yaml.

Check consistency with the SRS before returning the result.
```

## Verification checklist (Steps 1C and 1E)

- [x] Exactly 10 requirements, each with a unique ID and one API operation
- [x] Methods, endpoints, and status codes follow the input table
- [x] Every required field has a location and a type
- [x] YAML parses, follows OpenAPI 3.0.3, and has exactly 10 operations
- [x] Every operation has an `x-requirement-id`
- [x] Required path parameters have `required: true`
- [x] Running the validator on the baseline pair reports 0 findings

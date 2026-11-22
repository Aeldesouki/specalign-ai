# SpecAlign AI evaluation

Generated 2026-11-21 by `scripts/evaluate.py`.

## Validation results

| Dataset | Requirements | API operations | Findings |
| --- | --- | --- | --- |
| Baseline | 10 | 10 | 0 |
| Faulty | 10 | 9 | 6 |

Faulty dataset scored against `ground_truth.json` (a finding counts as correct when both its requirement ID and its rule match a known error):

- Known errors: 6
- Correctly detected: 6
- False positives: 0 
- Missed errors: 0 
- Precision: 1.00
- Recall: 1.00

| Finding | Requirement | Rule | Expected | Actual |
| --- | --- | --- | --- | --- |
| F001 | REQ-001 | ENDPOINT_MISMATCH | `/api/orders` | `/api/order` |
| F002 | REQ-002 | METHOD_MISMATCH | `GET` | `POST` |
| F003 | REQ-003 | STATUS_CODE_MISMATCH | `200` | `204` |
| F004 | REQ-004 | MISSING_OPERATION | `PATCH /api/orders/{orderId}/status` | `Missing` |
| F005 | REQ-005 | REQUIRED_FIELD_MISMATCH | `note:body:string` | `comment:body:string` |
| F006 | REQ-006 | FIELD_TYPE_MISMATCH | `reason:body:string` | `reason:body:integer` |

## RAG evaluation

Not run: Ollama is not reachable. Install it from https://ollama.com and make sure it is running. (ConnectionError)

## Limitations

- Supports only DOCX requirements tables and OpenAPI YAML.
- Assumes a predefined SRS table structure and `fieldName:location:type` notation.
- Supports simple inline request schemas with top-level required fields.
- Requires explicit `x-requirement-id` mappings.
- The dataset is small and synthetic; results show the approach works, not accuracy on real documents.
- AI-generated explanations require human review.

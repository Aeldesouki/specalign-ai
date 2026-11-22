"""Parse an SRS Word document and an OpenAPI YAML file into one common record format.

Both parsers return lists of plain dictionaries so the validator and the RAG index can
treat the two documents the same way. Every record keeps its source file name so that
findings and answers can cite where each value came from.

Common field format (used by both parsers):
    {"name": "orderId", "location": "path", "type": "string"}
"""
from __future__ import annotations

import os
import re
from typing import IO, Any, Union

import yaml
from docx import Document

FileInput = Union[str, os.PathLike, IO[bytes], IO[str]]

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
VALID_LOCATIONS = {"path", "query", "body"}

# Accepted header spellings for each SRS column, compared after normalising
# (lowercase, no spaces or punctuation). This tolerates small wording changes.
HEADER_ALIASES = {
    "requirement_id": {"requirementid", "reqid", "id"},
    "title": {"title", "requirementtitle", "operation", "name"},
    "description": {"description", "functionaldescription"},
    "method": {"httpmethod", "method"},
    "path": {"endpoint", "apiendpointpath", "apipath", "path", "endpointpath"},
    "success_status": {"successstatus", "expectedstatus", "expectedhttpsuccessstatus",
                       "status", "successcode"},
    "required_fields": {"requiredfields", "requiredrequestfields", "fields"},
}


class ParseError(ValueError):
    """Raised when a document does not have the structure SpecAlign AI expects."""


def _source_name(file: FileInput, source_name: str | None) -> str:
    if source_name:
        return source_name
    if isinstance(file, (str, os.PathLike)):
        return os.path.basename(os.fspath(file))
    return os.path.basename(getattr(file, "name", "uploaded_file"))


def _normalise_header(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def parse_field_list(text: str) -> list[dict[str, str]]:
    """Parse 'orderId:path:string; status:body:string' into field dictionaries.

    Raises ParseError if an entry does not follow fieldName:location:type.
    """
    fields = []
    for entry in text.split(";"):
        entry = entry.strip()
        if not entry or entry.lower() in {"none", "n/a", "-"}:
            continue
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) != 3 or not all(parts):
            raise ParseError(f"Field '{entry}' is not in fieldName:location:type format")
        name, location, ftype = parts
        location = location.lower()
        if location not in VALID_LOCATIONS:
            raise ParseError(f"Field '{entry}' has unknown location '{location}'")
        fields.append({"name": name, "location": location, "type": ftype.lower()})
    return fields


def _map_headers(header_cells: list[str]) -> dict[str, int] | None:
    """Return {canonical_column: index} if this row looks like the requirements header."""
    mapping = {}
    for index, cell in enumerate(header_cells):
        key = _normalise_header(cell)
        for canonical, aliases in HEADER_ALIASES.items():
            if key in aliases and canonical not in mapping:
                mapping[canonical] = index
                break
    required = {"requirement_id", "method", "path", "success_status", "required_fields"}
    return mapping if required <= mapping.keys() else None


def parse_requirements(file: FileInput, source_name: str | None = None) -> list[dict[str, Any]]:
    """Extract requirement records from the requirements table of a Word SRS.

    `file` may be a path or a binary file-like object (such as a Streamlit upload).
    """
    source = _source_name(file, source_name)
    document = Document(file)

    for table in document.tables:
        if not table.rows:
            continue
        header = [cell.text.strip() for cell in table.rows[0].cells]
        columns = _map_headers(header)
        if columns is None:
            continue

        requirements = []
        for row_number, row in enumerate(table.rows[1:], start=2):
            cells = [cell.text.strip() for cell in row.cells]
            if not any(cells):
                continue

            def col(name: str) -> str:
                index = columns.get(name)
                return cells[index] if index is not None and index < len(cells) else ""

            req_id = col("requirement_id").replace("\n", "").replace(" ", "")
            if not req_id:
                raise ParseError(f"{source}: table row {row_number} has no requirement ID")
            try:
                fields = parse_field_list(col("required_fields"))
            except ParseError as err:
                raise ParseError(f"{source}, {req_id}: {err}") from None

            requirements.append({
                "source": source,
                "requirement_id": req_id,
                "title": col("title"),
                "description": col("description"),
                "method": col("method").upper(),
                "path": col("path").replace(" ", ""),
                "success_status": col("success_status"),
                "required_fields": fields,
            })
        return requirements

    raise ParseError(f"{source}: no table with requirement ID, method, endpoint, status, "
                     "and required-field columns was found")


def _load_yaml(file: FileInput) -> Any:
    if isinstance(file, (str, os.PathLike)):
        with open(file, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    data = file.read()
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return yaml.safe_load(data)


def _required_parameters(parameters: list[dict]) -> list[dict[str, str]]:
    fields = []
    for param in parameters or []:
        location = param.get("in")
        if location not in {"path", "query"} or not param.get("required", False):
            continue
        fields.append({
            "name": param.get("name", ""),
            "location": location,
            "type": str((param.get("schema") or {}).get("type", "unknown")).lower(),
        })
    return fields


def _required_body_fields(operation: dict) -> list[dict[str, str]]:
    """Read top-level required fields from an inline application/json body schema."""
    content = (operation.get("requestBody") or {}).get("content") or {}
    schema = (content.get("application/json") or {}).get("schema") or {}
    properties = schema.get("properties") or {}
    fields = []
    for name in schema.get("required") or []:
        prop = properties.get(name) or {}
        fields.append({"name": name, "location": "body",
                       "type": str(prop.get("type", "unknown")).lower()})
    return fields


def parse_openapi(file: FileInput, source_name: str | None = None) -> list[dict[str, Any]]:
    """Extract one record per API operation from an OpenAPI 3.0 YAML file.

    Only inline JSON request schemas with top-level `required` lists are supported.
    `$ref` resolution, nested schemas, and multiple content types are out of scope.
    """
    source = _source_name(file, source_name)
    try:
        spec = _load_yaml(file)
    except yaml.YAMLError as err:
        raise ParseError(f"{source}: invalid YAML ({err})") from None
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
        raise ParseError(f"{source}: no 'paths' section found; is this an OpenAPI file?")

    operations = []
    for path, path_item in spec["paths"].items():
        if not isinstance(path_item, dict):
            continue
        shared_params = path_item.get("parameters") or []
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            # Operation-level parameters override path-level ones with the same name+location.
            params = {(p.get("name"), p.get("in")): p for p in shared_params}
            params.update({(p.get("name"), p.get("in")): p for p in operation.get("parameters") or []})

            operations.append({
                "source": source,
                "requirement_id": operation.get("x-requirement-id"),
                "operation_id": operation.get("operationId", f"{method}_{path}"),
                "summary": operation.get("summary", ""),
                "method": method.upper(),
                "path": path,
                "responses": [str(code) for code in (operation.get("responses") or {})],
                "required_fields": _required_parameters(list(params.values()))
                + _required_body_fields(operation),
            })
    return operations

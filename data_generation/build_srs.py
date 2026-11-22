"""Write the fictional SRS (requirements.docx) as a real Word table.

The requirement content was generated with Claude using the prompt in
claude_prompts.md (Step 1A). This script saves it with python-docx so the file
contains a genuine Word table rather than an image, as the project spec requires.

Usage:
    python data_generation/build_srs.py
"""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

ROOT = Path(__file__).resolve().parent.parent

HEADERS = ["Requirement ID", "Title", "Description", "HTTP Method", "Endpoint",
           "Success Status", "Required Fields"]

REQUIREMENTS = [
    ("REQ-001", "Create Order",
     "The system shall allow customers to create an order containing one or more items.",
     "POST", "/api/orders", "201", "customerId:body:string; items:body:array"),
    ("REQ-002", "Retrieve Order",
     "The system shall allow users to retrieve a single order by its identifier.",
     "GET", "/api/orders/{orderId}", "200", "orderId:path:string"),
    ("REQ-003", "List Customer Orders",
     "The system shall allow users to list all orders placed by a given customer.",
     "GET", "/api/customers/{customerId}/orders", "200", "customerId:path:string"),
    ("REQ-004", "Update Order Status",
     "The system shall allow authorized staff to update the status of an existing order.",
     "PATCH", "/api/orders/{orderId}/status", "200", "orderId:path:string; status:body:string"),
    ("REQ-005", "Add Order Note",
     "The system shall allow users to attach a text note to an existing order.",
     "POST", "/api/orders/{orderId}/notes", "201", "orderId:path:string; note:body:string"),
    ("REQ-006", "Create Return Request",
     "The system shall allow customers to request a return for an order, stating the reason.",
     "POST", "/api/returns", "201", "orderId:body:string; reason:body:string"),
    ("REQ-007", "Cancel Order",
     "The system shall allow customers to cancel an order that has not yet shipped.",
     "POST", "/api/orders/{orderId}/cancel", "200", "orderId:path:string"),
    ("REQ-008", "Retrieve Order Status",
     "The system shall allow users to view the current status of an order.",
     "GET", "/api/orders/{orderId}/status", "200", "orderId:path:string"),
    ("REQ-009", "Update Delivery Address",
     "The system shall allow customers to change the delivery address of an unshipped order.",
     "PATCH", "/api/orders/{orderId}/address", "200", "orderId:path:string; address:body:string"),
    ("REQ-010", "Retrieve Return",
     "The system shall allow users to retrieve the details of a return request.",
     "GET", "/api/returns/{returnId}", "200", "returnId:path:string"),
]


def build(path: Path) -> None:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)

    doc.add_heading("Software Requirements Specification", level=0)
    doc.add_paragraph("Online Order Management System · Version 1.0 · Fictional document "
                      "created for the SpecAlign AI portfolio project")

    doc.add_heading("1. Purpose", level=1)
    doc.add_paragraph(
        "This document defines the functional requirements for the Online Order Management "
        "System API. Each requirement describes one API operation, its HTTP method, endpoint, "
        "expected success status, and required request fields.")

    doc.add_heading("2. Field notation", level=1)
    doc.add_paragraph(
        "Required fields are written as fieldName:location:type, where location is path, "
        "query, or body. Multiple fields are separated by semicolons.")

    doc.add_heading("3. Functional requirements", level=1)
    table = doc.add_table(rows=1, cols=len(HEADERS))
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, HEADERS):
        cell.text = text
        cell.paragraphs[0].runs[0].bold = True
    for req in REQUIREMENTS:
        row = table.add_row().cells
        for cell, text in zip(row, req):
            cell.text = text
    for row in table.rows:
        row.cells[5].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    print(f"Wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    build(ROOT / "sample_docs" / "baseline" / "requirements.docx")

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.parsers import parse_openapi, parse_requirements  # noqa: E402

SAMPLES = ROOT / "sample_docs"


@pytest.fixture(scope="session")
def baseline():
    return (parse_requirements(SAMPLES / "baseline" / "requirements.docx"),
            parse_openapi(SAMPLES / "baseline" / "openapi.yaml"))


@pytest.fixture(scope="session")
def faulty():
    return (parse_requirements(SAMPLES / "faulty" / "requirements.docx"),
            parse_openapi(SAMPLES / "faulty" / "openapi.yaml"))


@pytest.fixture(scope="session")
def ground_truth():
    return json.loads((ROOT / "ground_truth.json").read_text(encoding="utf-8"))

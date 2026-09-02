from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def paradas() -> dict:
    """CRTM ParadasPorItinerario, trimmed to the properties the loader reads."""
    return json.loads((FIXTURES / "paradas.sample.json").read_text())

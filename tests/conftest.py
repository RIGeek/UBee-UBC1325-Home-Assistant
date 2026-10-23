"""Fixtures."""
from pathlib import Path

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURE = Path(__file__).parent / "fixtures" / "cmconnectionstatus.html"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def status_html() -> str:
    return FIXTURE.read_text()

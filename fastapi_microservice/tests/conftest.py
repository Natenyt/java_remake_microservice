"""Pytest configuration."""
import pytest
from fastapi.testclient import TestClient
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from main import app


@pytest.fixture
def client():
    """Test client for FastAPI."""
    return TestClient(app)


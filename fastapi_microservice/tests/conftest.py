"""
Pytest configuration for FastAPI tests.
"""
import pytest
from fastapi.testclient import TestClient
import sys
from pathlib import Path
# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from main import app


@pytest.fixture
def client():
    """Returns a test client for FastAPI."""
    return TestClient(app)


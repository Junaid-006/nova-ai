import os

import pytest

os.environ.setdefault("GEMINI_API_KEY", "test-api-key")

from backend import database


@pytest.fixture
def temporary_database(tmp_path, monkeypatch):
    database_path = tmp_path / "nova_ai_test.db"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    database.initialize_database()
    return database_path

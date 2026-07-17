import sqlite3
from pathlib import Path

from app import repository as repository_module
from app.repository import TursoRepository


class LocalLibsql:
    """Use SQLite's DB-API locally to exercise the libsql repository contract."""

    @staticmethod
    def connect(database_url: str, *, auth_token: str):
        assert auth_token == "test-token"
        return sqlite3.connect(database_url)


def test_turso_repository_uses_db_api_cursor_and_persists_changes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(repository_module, "libsql", LocalLibsql)
    repo = TursoRepository(str(tmp_path / "turso.sqlite"), "test-token")

    assert repo.company("C001")["CompanyName"] == "Northwind Pumps"
    assert repo.user("maya@northwind.example", "C001")["Name"] == "Maya Chen"

    assert repo.save_response("C001", "Q001", "Hello", "maya@example.com") is True
    assert repo.responses_for("C001")["Q001"] == "Hello"

    history = repo.rows("ResponseHistory")
    assert history[0]["NewValue"] == "Hello"

    repo.submit("C001", "maya@example.com")
    assert repo.company("C001")["Status"] == "Submitted"

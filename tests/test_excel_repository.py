import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.repository import SQLiteRepository


def test_sqlite_repository_round_trips_responses(tmp_path: Path) -> None:
    db_path = tmp_path / "sample.sqlite"
    repo = SQLiteRepository(db_path)

    assert repo.company("C001")["CompanyName"] == "Northwind Pumps"
    assert repo.user("maya@northwind.example", "C001")["Name"] == "Maya Chen"

    repo.save_response("C001", "Q001", "Hello", "maya@example.com")

    assert repo.responses_for("C001")["Q001"] == "Hello"
    assert repo.company("C001")["LastUpdated"]


def test_sqlite_repository_reloads_changes_from_disk(tmp_path: Path) -> None:
    db_path = tmp_path / "external-change.sqlite"
    repo = SQLiteRepository(db_path)

    assert repo.responses_for("C001") == {}

    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO Responses (CompanyID, QuestionID, ResponseValue, LastModifiedBy, LastModifiedTime) VALUES (?, ?, ?, ?, ?)", ("C001", "Q001", "Updated from disk", "", ""))

    repo.clear_cache()

    assert repo.responses_for("C001")["Q001"] == "Updated from disk"


def test_sqlite_repository_supports_concurrent_access(tmp_path: Path) -> None:
    db_path = tmp_path / "thread-safe.sqlite"
    repo = SQLiteRepository(db_path)

    def read_company_name(_: int) -> str:
        return repo.company("C001")["CompanyName"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(read_company_name, range(2)))

    assert results == ["Northwind Pumps", "Northwind Pumps"]

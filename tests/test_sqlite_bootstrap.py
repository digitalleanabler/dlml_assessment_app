import sqlite3
from pathlib import Path

from app.repository import SQLiteRepository


def test_sqlite_repository_initializes_design_tables_but_leaves_response_tables_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "bootstrap.sqlite"
    repo = SQLiteRepository(db_path)

    with sqlite3.connect(db_path) as conn:
        company_count = conn.execute('SELECT COUNT(*) FROM "Companies"').fetchone()[0]
        question_count = conn.execute('SELECT COUNT(*) FROM "Questions"').fetchone()[0]
        response_count = conn.execute('SELECT COUNT(*) FROM "Responses"').fetchone()[0]
        history_count = conn.execute('SELECT COUNT(*) FROM "ResponseHistory"').fetchone()[0]

    assert company_count > 0
    assert question_count > 0
    assert response_count == 0
    assert history_count == 0

    assert repo.design_data()["Pages"]
    assert repo.responses_for("C001") == {}

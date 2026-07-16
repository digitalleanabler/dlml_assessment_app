# Author: Tanakorn Tantanawat

"""Google Sheets persistence, with a developer-only in-memory fallback."""

from __future__ import annotations

import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from libsql_client import ClientSync, create_client_sync
except ImportError:  # pragma: no cover - optional dependency for cloud mode
    ClientSync = Any  # type: ignore[assignment]
    create_client_sync = None  # type: ignore[assignment]
    LIBSQL_IMPORT_ERROR = ImportError("libsql-client is required for TursoRepository. Install it with pip install libsql-client.")
else:
    LIBSQL_IMPORT_ERROR = None


SHEETS = ("Companies", "Users", "Pages", "Questions", "QuestionOptions", "QuestionConditions", "Responses", "ResponseHistory")
STATIC_TABLES = {"Pages", "Questions", "QuestionOptions", "QuestionConditions"}
SHEET_HEADERS = {
    "Companies": ["CompanyID", "CompanyName", "Status", "LastUpdated", "SubmittedBy", "SubmittedTime"],
    "Users": ["Email", "Name", "CompanyID"],
    "Pages": ["PageID", "PageTitle"],
    "Questions": ["QuestionID", "PageID", "Sequence", "QuestionText", "AnswerType", "Required", "Active"],
    "QuestionOptions": ["OptionID", "QuestionID", "DisplayOrder", "OptionValue", "DisplayText"],
    "QuestionConditions": ["LogicID", "QuestionID", "Seq", "LeftParen", "DependsOnQuestion", "Operator", "ExpectedValue", "RightParen", "LogicalWithNext"],
    "Responses": ["CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime"],
    "ResponseHistory": ["HistoryID", "CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime"],
}


def default_database_path() -> Path:
    return Path(__file__).resolve().parent.parent / "db" / "dlmlassessmentdb.sqlite"


def default_workbook_path() -> Path:
    return default_database_path()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


SEED: dict[str, list[dict[str, str]]] = {
    "Companies": [
        {"CompanyID": "C001", "CompanyName": "Northwind Pumps", "Status": "Draft", "LastUpdated": "", "SubmittedBy": "", "SubmittedTime": ""},
        {"CompanyID": "C002", "CompanyName": "Contoso Manufacturing", "Status": "Draft", "LastUpdated": "", "SubmittedBy": "", "SubmittedTime": ""},
    ],
    "Users": [
        {"Email": "maya@northwind.example", "Name": "Maya Chen", "CompanyID": "C001"},
        {"Email": "noah@northwind.example", "Name": "Noah Patel", "CompanyID": "C001"},
        {"Email": "sofia@contoso.example", "Name": "Sofia Reyes", "CompanyID": "C002"},
    ],
    "Pages": [
        {"PageID": "P001", "PageTitle": "Business Goal"},
        {"PageID": "P002", "PageTitle": "Business Objectives"},
    ],
    "Questions": [
        {"QuestionID": "Q001", "PageID": "P001", "Sequence": "1", "QuestionText": "Describe your company", "AnswerType": "Text", "Required": "TRUE", "Active": "TRUE"},
        {"QuestionID": "Q002", "PageID": "P002", "Sequence": "2", "QuestionText": "Lean implementation level", "AnswerType": "Choice", "Required": "TRUE", "Active": "TRUE"},
        {"QuestionID": "Q003", "PageID": "P002", "Sequence": "3", "QuestionText": "Is TPM implemented?", "AnswerType": "Choice", "Required": "TRUE", "Active": "TRUE"},
    ],
    "QuestionOptions": [
        {"OptionID": "O002A", "QuestionID": "Q002", "DisplayOrder": "1", "OptionValue": "NONE", "DisplayText": "None"},
        {"OptionID": "O002B", "QuestionID": "Q002", "DisplayOrder": "2", "OptionValue": "SOME", "DisplayText": "Some"},
        {"OptionID": "O002C", "QuestionID": "Q002", "DisplayOrder": "3", "OptionValue": "ALL", "DisplayText": "All"},
        {"OptionID": "O003A", "QuestionID": "Q003", "DisplayOrder": "1", "OptionValue": "NO", "DisplayText": "No"},
        {"OptionID": "O003B", "QuestionID": "Q003", "DisplayOrder": "2", "OptionValue": "YES", "DisplayText": "Yes"},
    ],
    "QuestionConditions": [
        {"LogicID": "L001", "QuestionID": "Q003", "Seq": "1", "LeftParen": "", "DependsOnQuestion": "Q002", "Operator": "=", "ExpectedValue": "ALL", "RightParen": "", "LogicalWithNext": "END"}
    ],
    "Responses": [],
    "ResponseHistory": [],
}


class SQLiteRepository:
    """Repository backed by the local SQLite database for local development."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path or default_database_path()).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._rows_cache: dict[str, list[dict[str, str]]] = {}
        self._design_cache: dict[str, Any] | None = None
        self._login_cache: dict[str, list[dict[str, str]]] = {}
        self._history_counter = 0
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            for sheet_name in SHEETS:
                headers = SHEET_HEADERS.get(sheet_name, [])
                if not headers:
                    continue
                columns = ", ".join(f'"{header}" TEXT' for header in headers)
                conn.execute(f'CREATE TABLE IF NOT EXISTS "{sheet_name}" ({columns})')

            for sheet_name in SHEETS:
                existing = conn.execute(f'SELECT COUNT(*) FROM "{sheet_name}"').fetchone()[0]
                if existing:
                    continue
                headers = SHEET_HEADERS.get(sheet_name, [])
                if not headers:
                    continue
                seed_rows = SEED.get(sheet_name, [])
                if not seed_rows:
                    continue
                placeholders = ", ".join("?" for _ in headers)
                insert_sql = f'INSERT INTO "{sheet_name}" ({", ".join(f'"{header}"' for header in headers)}) VALUES ({placeholders})'
                for row in seed_rows:
                    values = [row.get(header, "") for header in headers]
                    conn.execute(insert_sql, values)

    def _rows_for_sheet(self, worksheet: str) -> list[dict[str, str]]:
        with self._connect() as conn:
            cursor = conn.execute(f'SELECT * FROM "{worksheet}"')
            rows = []
            for row in cursor.fetchall():
                payload = {key: "" if value is None else str(value) for key, value in dict(row).items()}
                if not payload or all(str(value).strip() == "" for value in payload.values()):
                    continue
                rows.append(payload)
            return rows

    def rows(self, worksheet: str) -> list[dict[str, str]]:
        if worksheet in STATIC_TABLES:
            if worksheet not in self._rows_cache:
                self._rows_cache[worksheet] = deepcopy(self._rows_for_sheet(worksheet))
            return deepcopy(self._rows_cache[worksheet])
        return deepcopy(self._rows_for_sheet(worksheet))

    def clear_cache(self) -> None:
        self._rows_cache.clear()
        self._design_cache = None
        self._login_cache.clear()
        self._history_counter = 0

    def load_login_data(self, force: bool = False) -> None:
        if self._login_cache and not force:
            return
        self._login_cache = {
            "Companies": self.rows("Companies"),
            "Users": self.rows("Users"),
        }

    def design_data(self) -> dict[str, Any]:
        if self._design_cache is None:
            questions = self.rows("Questions")
            options_by_question: dict[str, list[dict[str, str]]] = {}
            conditions_by_question: dict[str, list[dict[str, str]]] = {}
            for row in self.rows("QuestionOptions"):
                options_by_question.setdefault(row["QuestionID"], []).append(row)
            for row in self.rows("QuestionConditions"):
                conditions_by_question.setdefault(row["QuestionID"], []).append(row)
            try:
                pages = self.rows("Pages")
            except KeyError:
                pages = []
            self._design_cache = {
                "Pages": pages,
                "Questions": questions,
                "OptionsByQuestion": options_by_question,
                "ConditionsByQuestion": conditions_by_question,
            }
        return self._design_cache

    def _find_row(self, worksheet: str, predicate) -> tuple[int | None, dict[str, str] | None]:
        rows = self.rows(worksheet)
        for row_number, row in enumerate(rows, start=1):
            if predicate(row):
                return row_number, row
        return None, None

    def _write_value(self, worksheet: str, row_number: int, header: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(f'UPDATE "{worksheet}" SET "{header}" = ? WHERE rowid = ?', (value, row_number))

    def company(self, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Companies"] if row["CompanyID"] == company_id), None)

    def company_by_name(self, company_name: str) -> dict[str, str] | None:
        self.load_login_data()
        target = company_name.strip().casefold()
        return next((row for row in self._login_cache["Companies"] if row["CompanyName"].strip().casefold() == target), None)

    def user(self, email: str, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Users"] if row["Email"].lower() == email.lower() and row["CompanyID"] == company_id), None)

    def responses_for(self, company_id: str) -> dict[str, str]:
        return {row["QuestionID"]: row["ResponseValue"] for row in self.rows("Responses") if row["CompanyID"] == company_id}

    def save_response(self, company_id: str, question_id: str, value: str, email: str) -> bool:
        with self._connect() as conn:
            existing = conn.execute('SELECT "ResponseValue" FROM "Responses" WHERE "CompanyID" = ? AND "QuestionID" = ?', (company_id, question_id)).fetchone()
            old = existing[0] if existing else ""
            if old == value:
                return False
            stamp = now()
            if existing:
                conn.execute('UPDATE "Responses" SET "ResponseValue" = ?, "LastModifiedBy" = ?, "LastModifiedTime" = ? WHERE "CompanyID" = ? AND "QuestionID" = ?', (value, email, stamp, company_id, question_id))
            else:
                conn.execute('INSERT INTO "Responses" ("CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime") VALUES (?, ?, ?, ?, ?)', (company_id, question_id, value, email, stamp))
            self._update_company_last_updated(company_id, stamp, conn=conn)
            self.append_response_history(company_id, question_id, old, value, email, stamp, conn=conn)
        self.clear_cache()
        return True

    def _update_company_last_updated(self, company_id: str, stamp: str, conn: sqlite3.Connection | None = None) -> None:
        connection = conn or self._connect()
        try:
            connection.execute('UPDATE "Companies" SET "LastUpdated" = ? WHERE "CompanyID" = ?', (stamp, company_id))
            if conn is None:
                connection.commit()
        finally:
            if conn is None:
                connection.close()

    def append_response_history(self, company_id: str, question_id: str, old_value: str, new_value: str, email: str, stamp: str, conn: sqlite3.Connection | None = None) -> None:
        self._history_counter += 1
        next_id = f"H{self._history_counter:04}"
        connection = conn or self._connect()
        try:
            connection.execute('INSERT INTO "ResponseHistory" ("HistoryID", "CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime") VALUES (?, ?, ?, ?, ?, ?, ?)', (next_id, company_id, question_id, old_value, new_value, email, stamp))
            if conn is None:
                connection.commit()
        finally:
            if conn is None:
                connection.close()

    def submit(self, company_id: str, email: str) -> None:
        company = self.company(company_id)
        if company is None:
            raise ValueError("Company does not exist")
        stamp = now()
        with self._connect() as conn:
            conn.execute('UPDATE "Companies" SET "Status" = ?, "LastUpdated" = ?, "SubmittedBy" = ?, "SubmittedTime" = ? WHERE "CompanyID" = ?', ("Submitted", stamp, email, stamp, company_id))
        self.clear_cache()


class TursoRepository(SQLiteRepository):
    """Repository backed by a remote Turso database for cloud deployment."""

    def __init__(self, database_url: str | None = None, auth_token: str | None = None) -> None:
        self.database_url = database_url or ""
        self.auth_token = auth_token or ""
        self._client: ClientSync | None = None
        self._rows_cache: dict[str, list[dict[str, str]]] = {}
        self._design_cache: dict[str, Any] | None = None
        self._login_cache: dict[str, list[dict[str, str]]] = {}
        self._history_counter = 0
        self._initialize_schema()

    def _connect(self) -> ClientSync:
        if self._client is None:
            if not self.database_url:
                raise ValueError("Turso database URL is required")
            self._client = create_client_sync(self.database_url, auth_token=self.auth_token) if self.auth_token else create_client_sync(self.database_url)
        return self._client

    def _initialize_schema(self) -> None:
        conn = self._connect()
        for sheet_name in SHEETS:
            headers = SHEET_HEADERS.get(sheet_name, [])
            if not headers:
                continue
            columns = ", ".join(f'"{header}" TEXT' for header in headers)
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{sheet_name}" ({columns})')

        for sheet_name in SHEETS:
            existing_result = conn.execute(f'SELECT COUNT(*) AS row_count FROM "{sheet_name}"')
            existing = int(existing_result.rows[0][0]) if getattr(existing_result, "rows", None) else 0
            if existing:
                continue
            headers = SHEET_HEADERS.get(sheet_name, [])
            if not headers:
                continue
            seed_rows = SEED.get(sheet_name, [])
            if not seed_rows:
                continue
            placeholders = ", ".join("?" for _ in headers)
            insert_sql = f'INSERT INTO "{sheet_name}" ({", ".join(f'"{header}"' for header in headers)}) VALUES ({placeholders})'
            for row in seed_rows:
                values = [row.get(header, "") for header in headers]
                conn.execute(insert_sql, values)

    def _rows_for_sheet(self, worksheet: str) -> list[dict[str, str]]:
        conn = self._connect()
        result = conn.execute(f'SELECT * FROM "{worksheet}"')
        rows = []
        for row in getattr(result, "rows", []) or []:
            payload = {key: "" if value is None else str(value) for key, value in dict(row).items()}
            if not payload or all(str(value).strip() == "" for value in payload.values()):
                continue
            rows.append(payload)
        return rows

    def rows(self, worksheet: str) -> list[dict[str, str]]:
        if worksheet in STATIC_TABLES:
            if worksheet not in self._rows_cache:
                self._rows_cache[worksheet] = deepcopy(self._rows_for_sheet(worksheet))
            return deepcopy(self._rows_cache[worksheet])
        return deepcopy(self._rows_for_sheet(worksheet))

    def clear_cache(self) -> None:
        self._rows_cache.clear()
        self._design_cache = None
        self._login_cache.clear()
        self._history_counter = 0

    def load_login_data(self, force: bool = False) -> None:
        if self._login_cache and not force:
            return
        self._login_cache = {
            "Companies": self.rows("Companies"),
            "Users": self.rows("Users"),
        }

    def design_data(self) -> dict[str, Any]:
        if self._design_cache is None:
            questions = self.rows("Questions")
            options_by_question: dict[str, list[dict[str, str]]] = {}
            conditions_by_question: dict[str, list[dict[str, str]]] = {}
            for row in self.rows("QuestionOptions"):
                options_by_question.setdefault(row["QuestionID"], []).append(row)
            for row in self.rows("QuestionConditions"):
                conditions_by_question.setdefault(row["QuestionID"], []).append(row)
            try:
                pages = self.rows("Pages")
            except KeyError:
                pages = []
            self._design_cache = {
                "Pages": pages,
                "Questions": questions,
                "OptionsByQuestion": options_by_question,
                "ConditionsByQuestion": conditions_by_question,
            }
        return self._design_cache

    def company(self, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Companies"] if row["CompanyID"] == company_id), None)

    def company_by_name(self, company_name: str) -> dict[str, str] | None:
        self.load_login_data()
        target = company_name.strip().casefold()
        return next((row for row in self._login_cache["Companies"] if row["CompanyName"].strip().casefold() == target), None)

    def user(self, email: str, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Users"] if row["Email"].lower() == email.lower() and row["CompanyID"] == company_id), None)

    def responses_for(self, company_id: str) -> dict[str, str]:
        return {row["QuestionID"]: row["ResponseValue"] for row in self.rows("Responses") if row["CompanyID"] == company_id}

    def save_response(self, company_id: str, question_id: str, value: str, email: str) -> bool:
        conn = self._connect()
        existing_result = conn.execute('SELECT "ResponseValue" FROM "Responses" WHERE "CompanyID" = ? AND "QuestionID" = ?', (company_id, question_id))
        existing = existing_result.rows[0][0] if getattr(existing_result, "rows", None) else None
        old = existing or ""
        if old == value:
            return False
        stamp = now()
        if existing is not None:
            conn.execute('UPDATE "Responses" SET "ResponseValue" = ?, "LastModifiedBy" = ?, "LastModifiedTime" = ? WHERE "CompanyID" = ? AND "QuestionID" = ?', (value, email, stamp, company_id, question_id))
        else:
            conn.execute('INSERT INTO "Responses" ("CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime") VALUES (?, ?, ?, ?, ?)', (company_id, question_id, value, email, stamp))
        self._update_company_last_updated(company_id, stamp, conn=conn)
        self.append_response_history(company_id, question_id, old, value, email, stamp, conn=conn)
        self.clear_cache()
        return True

    def _update_company_last_updated(self, company_id: str, stamp: str, conn: ClientSync | None = None) -> None:
        connection = conn or self._connect()
        connection.execute('UPDATE "Companies" SET "LastUpdated" = ? WHERE "CompanyID" = ?', (stamp, company_id))

    def append_response_history(self, company_id: str, question_id: str, old_value: str, new_value: str, email: str, stamp: str, conn: ClientSync | None = None) -> None:
        self._history_counter += 1
        next_id = f"H{self._history_counter:04}"
        connection = conn or self._connect()
        connection.execute('INSERT INTO "ResponseHistory" ("HistoryID", "CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime") VALUES (?, ?, ?, ?, ?, ?, ?)', (next_id, company_id, question_id, old_value, new_value, email, stamp))

    def submit(self, company_id: str, email: str) -> None:
        company = self.company(company_id)
        if company is None:
            raise ValueError("Company does not exist")
        stamp = now()
        conn = self._connect()
        conn.execute('UPDATE "Companies" SET "Status" = ?, "LastUpdated" = ?, "SubmittedBy" = ?, "SubmittedTime" = ? WHERE "CompanyID" = ?', ("Submitted", stamp, email, stamp, company_id))
        self.clear_cache()


class ExcelRepository(SQLiteRepository):
    """Backward-compatible alias for the SQLite-backed repository."""

    pass


class InMemoryRepository:
    """For local UI testing only. Each Streamlit session receives its own copy."""

    def __init__(self) -> None:
        self.tables = deepcopy(SEED)
        self._static_cache: dict[str, list[dict[str, str]]] = {}
        self._design_cache: dict[str, Any] | None = None
        self._login_cache: dict[str, list[dict[str, str]]] = {}
        self._history_counter = 0

    def rows(self, worksheet: str) -> list[dict[str, str]]:
        if worksheet in STATIC_TABLES:
            if worksheet not in self._static_cache:
                self._static_cache[worksheet] = deepcopy(self.tables[worksheet])
            return deepcopy(self._static_cache[worksheet])
        return self.tables[worksheet]

    def clear_cache(self) -> None:
        self._static_cache.clear()
        self._design_cache = None
        self._login_cache.clear()
        self._history_counter = 0

    def load_login_data(self, force: bool = False) -> None:
        if self._login_cache and not force:
            return
        self._login_cache = {
            "Companies": self.rows("Companies"),
            "Users": self.rows("Users"),
        }

    def design_data(self) -> dict[str, Any]:
        if self._design_cache is None:
            questions = self.rows("Questions")
            options_by_question: dict[str, list[dict[str, str]]] = {}
            conditions_by_question: dict[str, list[dict[str, str]]] = {}
            for row in self.rows("QuestionOptions"):
                options_by_question.setdefault(row["QuestionID"], []).append(row)
            for row in self.rows("QuestionConditions"):
                conditions_by_question.setdefault(row["QuestionID"], []).append(row)
            try:
                pages = self.rows("Pages")
            except KeyError:
                pages = []
            self._design_cache = {
                "Pages": pages,
                "Questions": questions,
                "OptionsByQuestion": options_by_question,
                "ConditionsByQuestion": conditions_by_question,
            }
        return self._design_cache

    def company(self, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Companies"] if row["CompanyID"] == company_id), None)

    def company_by_name(self, company_name: str) -> dict[str, str] | None:
        self.load_login_data()
        target = company_name.strip().casefold()
        return next((row for row in self._login_cache["Companies"] if row["CompanyName"].strip().casefold() == target), None)

    def user(self, email: str, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Users"] if row["Email"].lower() == email.lower() and row["CompanyID"] == company_id), None)

    def responses_for(self, company_id: str) -> dict[str, str]:
        return {row["QuestionID"]: row["ResponseValue"] for row in self.rows("Responses") if row["CompanyID"] == company_id}

    def save_response(self, company_id: str, question_id: str, value: str, email: str) -> bool:
        existing = next((row for row in self.rows("Responses") if row["CompanyID"] == company_id and row["QuestionID"] == question_id), None)
        old = existing["ResponseValue"] if existing else ""
        if old == value:
            return False
        stamp = now()
        if existing:
            existing.update({"ResponseValue": value, "LastModifiedBy": email, "LastModifiedTime": stamp})
        else:
            self.rows("Responses").append({"CompanyID": company_id, "QuestionID": question_id, "ResponseValue": value, "LastModifiedBy": email, "LastModifiedTime": stamp})
        self.append_response_history(company_id, question_id, old, value, email, stamp)
        return True

    def append_response_history(self, company_id: str, question_id: str, old_value: str, new_value: str, email: str, stamp: str) -> None:
        self._history_counter += 1
        self.rows("ResponseHistory").append({"HistoryID": f"H{self._history_counter:04}", "CompanyID": company_id, "QuestionID": question_id, "OldValue": old_value, "NewValue": new_value, "ModifiedBy": email, "ModifiedTime": stamp})

    def submit(self, company_id: str, email: str) -> None:
        company = self.company(company_id)
        assert company
        stamp = now()
        company.update({"Status": "Submitted", "LastUpdated": stamp, "SubmittedBy": email, "SubmittedTime": stamp})


class GoogleSheetsRepository:
    """Repository for the production Google Spreadsheet contract."""

    def __init__(self, spreadsheet: Any) -> None:
        self.spreadsheet = spreadsheet
        self._rows_cache: dict[str, list[dict[str, str]]] = {}
        self._design_cache: dict[str, Any] | None = None
        self._login_cache: dict[str, list[dict[str, str]]] = {}
        self._history_counter = 0

    @classmethod
    def from_service_account(cls, service_account_info: dict[str, Any], spreadsheet_id: str) -> "GoogleSheetsRepository":
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        credentials = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        return cls(gspread.authorize(credentials).open_by_key(spreadsheet_id))

    def rows(self, worksheet: str) -> list[dict[str, str]]:
        if worksheet not in self._rows_cache:
            self._rows_cache[worksheet] = self.spreadsheet.worksheet(worksheet).get_all_records(default_blank="")
        return deepcopy(self._rows_cache[worksheet])

    def clear_cache(self) -> None:
        self._rows_cache.clear()
        self._design_cache = None
        self._login_cache.clear()
        self._history_counter = 0

    def load_login_data(self, force: bool = False) -> None:
        if force:
            self._rows_cache.pop("Companies", None)
            self._rows_cache.pop("Users", None)
            self._login_cache.clear()
        if self._login_cache and not force:
            return
        self._login_cache = {
            "Companies": self.rows("Companies"),
            "Users": self.rows("Users"),
        }

    def _worksheet(self, name: str):
        return self.spreadsheet.worksheet(name)

    def design_data(self) -> dict[str, Any]:
        if self._design_cache is None:
            questions = self.rows("Questions")
            options_by_question: dict[str, list[dict[str, str]]] = {}
            conditions_by_question: dict[str, list[dict[str, str]]] = {}
            for row in self.rows("QuestionOptions"):
                options_by_question.setdefault(row["QuestionID"], []).append(row)
            for row in self.rows("QuestionConditions"):
                conditions_by_question.setdefault(row["QuestionID"], []).append(row)
            try:
                pages = self.rows("Pages")
            except KeyError:
                pages = []
            self._design_cache = {
                "Pages": pages,
                "Questions": questions,
                "OptionsByQuestion": options_by_question,
                "ConditionsByQuestion": conditions_by_question,
            }
        return self._design_cache

    def _find_row(self, worksheet: str, predicate) -> tuple[int, dict[str, str]] | tuple[None, None]:
        for row_number, row in enumerate(self.rows(worksheet), start=2):
            if predicate(row):
                return row_number, row
        return None, None

    def company(self, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Companies"] if row["CompanyID"] == company_id), None)

    def company_by_name(self, company_name: str) -> dict[str, str] | None:
        self.load_login_data()
        target = company_name.strip().casefold()
        return next((row for row in self._login_cache["Companies"] if row["CompanyName"].strip().casefold() == target), None)

    def user(self, email: str, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Users"] if row["Email"].lower() == email.lower() and row["CompanyID"] == company_id), None)

    def responses_for(self, company_id: str) -> dict[str, str]:
        return {row["QuestionID"]: row["ResponseValue"] for row in self.rows("Responses") if row["CompanyID"] == company_id}

    def save_response(self, company_id: str, question_id: str, value: str, email: str) -> bool:
        row_number, current = self._find_row("Responses", lambda row: row["CompanyID"] == company_id and row["QuestionID"] == question_id)
        old = current["ResponseValue"] if current else ""
        if old == value:
            return False
        stamp = now()
        sheet = self._worksheet("Responses")
        if current:
            headers = sheet.row_values(1)
            updated = {**current, "ResponseValue": value, "LastModifiedBy": email, "LastModifiedTime": stamp}
            sheet.update(f"A{row_number}", [[updated.get(header, "") for header in headers]])
        else:
            sheet.append_row([company_id, question_id, value, email, stamp], value_input_option="USER_ENTERED")
        self._update_company_last_updated(company_id, stamp)
        self.append_response_history(company_id, question_id, old, value, email, stamp)
        self._rows_cache.pop("Responses", None)
        return True

    def _update_company_last_updated(self, company_id: str, stamp: str) -> None:
        company_row_number, current = self._find_row("Companies", lambda row: row["CompanyID"] == company_id)
        if not current:
            return
        sheet = self._worksheet("Companies")
        headers = sheet.row_values(1)
        updated = {**current, "LastUpdated": stamp}
        sheet.update(f"A{company_row_number}", [[updated.get(header, "") for header in headers]])
        self._rows_cache.pop("Companies", None)
        if "Companies" in self._login_cache:
            for row in self._login_cache["Companies"]:
                if row["CompanyID"] == company_id:
                    row.update({"LastUpdated": stamp})
                    break

    def append_response_history(self, company_id: str, question_id: str, old_value: str, new_value: str, email: str, stamp: str) -> None:
        self._history_counter += 1
        history = self._worksheet("ResponseHistory")
        history.append_row([f"H{self._history_counter:04}", company_id, question_id, old_value, new_value, email, stamp], value_input_option="USER_ENTERED")

    def submit(self, company_id: str, email: str) -> None:
        row_number, current = self._find_row("Companies", lambda row: row["CompanyID"] == company_id)
        if not current:
            raise ValueError("Company does not exist")
        stamp = now()
        sheet = self._worksheet("Companies")
        headers = sheet.row_values(1)
        updated = {**current, "Status": "Submitted", "LastUpdated": stamp, "SubmittedBy": email, "SubmittedTime": stamp}
        sheet.update(f"A{row_number}", [[updated.get(header, "") for header in headers]])
        self._rows_cache.pop("Companies", None)
        if "Companies" in self._login_cache:
            for row in self._login_cache["Companies"]:
                if row["CompanyID"] == company_id:
                    row.update({"Status": "Submitted", "LastUpdated": stamp, "SubmittedBy": email, "SubmittedTime": stamp})
                    break

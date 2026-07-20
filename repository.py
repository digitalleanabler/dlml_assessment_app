# Author: Tanakorn Tantanawat

"""Repository implementations for local SQLite, Turso, and in-memory use."""

from __future__ import annotations

import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

from datetime import datetime

try:
    import libsql
except Exception as exc:  # pragma: no cover - optional dependency for cloud mode
    libsql = None  # type: ignore[assignment]
    LIBSQL_IMPORT_ERROR = exc
else:
    LIBSQL_IMPORT_ERROR = None


def libsql_available() -> bool:
    return libsql is not None


SHEETS = ("Companies", "Users", "Pages", "Questions", "QuestionOptions", "QuestionConditions", "QuestionVisibility", "Responses", "ResponseHistory")
STATIC_TABLES = {"Pages", "Questions", "QuestionOptions", "QuestionConditions"}
SHEET_HEADERS = {
    "Companies": ["CompanyID", "CompanyName", "Status", "LastUpdated", "SubmittedBy", "SubmittedTime"],
    "Users": ["Email", "Name", "CompanyID"],
    "Pages": ["PageID", "PageTitle"],
    "Questions": ["QuestionID", "PageID", "Sequence", "QuestionText", "AnswerType", "Required"],
    "QuestionOptions": ["OptionID", "QuestionID", "DisplayOrder", "OptionValue", "DisplayText"],
    "QuestionConditions": ["LogicID", "QuestionID", "Seq", "LeftParen", "DependsOnQuestion", "Operator", "ExpectedValue", "RightParen", "LogicalWithNext"],
    "QuestionVisibility": ["CompanyID", "QuestionID", "Visible"],
    "Responses": ["CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime"],
    "ResponseHistory": ["CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime"],
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
        {"Email": "alex@northwind.example", "Name": "Alex Rivera", "CompanyID": "C001"},
        {"Email": "sara@contoso.example", "Name": "Sara Kim", "CompanyID": "C002"},
    ],
    "Pages": [
        {"PageID": "P001", "PageTitle": "Business Goal"},
        {"PageID": "P002", "PageTitle": "Business Objectives"},
    ],
    "Questions": [
        {"QuestionID": "Q001", "PageID": "P001", "Sequence": "1", "QuestionText": "Describe your products?", "AnswerType": "Text", "Required": "TRUE"},
        {"QuestionID": "Q002", "PageID": "P002", "Sequence": "2", "QuestionText": "Lean implementation level?", "AnswerType": "Choice", "Required": "TRUE"},
        {"QuestionID": "Q003", "PageID": "P002", "Sequence": "3", "QuestionText": "TPM implemented?", "AnswerType": "Choice", "Required": "TRUE"},
    ],
    "QuestionOptions": [
        {"OptionID": "O002A", "QuestionID": "Q002", "DisplayOrder": "1", "OptionValue": "NONE", "DisplayText": "None"},
        {"OptionID": "O002B", "QuestionID": "Q002", "DisplayOrder": "2", "OptionValue": "SOME", "DisplayText": "Some"},
        {"OptionID": "O002C", "QuestionID": "Q002", "DisplayOrder": "3", "OptionValue": "ALL", "DisplayText": "All"},
        {"OptionID": "O003A", "QuestionID": "Q003", "DisplayOrder": "1", "OptionValue": "NO", "DisplayText": "No"},
        {"OptionID": "O003B", "QuestionID": "Q003", "DisplayOrder": "2", "OptionValue": "YES", "DisplayText": "Yes"},
    ],
    "QuestionConditions": [
        {"LogicID": "L001", "QuestionID": "Q003", "Seq": "1", "LeftParen": "", "DependsOnQuestion": "Q002", "Operator": "=", "ExpectedValue": "ALL", "RightParen": "", "LogicalWithNext": "END"},
    ],
    "QuestionVisibility": [],
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
                if sheet_name in {"Responses", "ResponseHistory"}:
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

    def _ensure_company_runtime_rows(self, company_id: str, questions: list[dict[str, str]] | None = None, responses: dict[str, str] | None = None, conn: sqlite3.Connection | None = None) -> None:
        company_id = str(company_id or "").strip()
        if not company_id:
            return
        if questions is None:
            questions = self.rows("Questions")
        if not questions:
            return

        current_responses = dict(responses or self.responses_for(company_id))
        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        connection = conn or self._connect()
        try:
            for question in questions:
                question_id = str(question.get("QuestionID", "") or "").strip()
                if not question_id:
                    continue

                try:
                    from .condition_engine import is_question_visible
                except ImportError:  # pragma: no cover - support running module directly
                    from condition_engine import is_question_visible

                visible = is_question_visible(question, conditions_by_question.get(question_id, []), current_responses)
                visible_value = "TRUE" if visible else "FALSE"
                existing_visibility = connection.execute('SELECT 1 FROM "QuestionVisibility" WHERE "CompanyID" = ? AND "QuestionID" = ?', (company_id, question_id)).fetchone()
                if existing_visibility is None:
                    connection.execute('INSERT INTO "QuestionVisibility" ("CompanyID", "QuestionID", "Visible") VALUES (?, ?, ?)', (company_id, question_id, visible_value))
                else:
                    connection.execute('UPDATE "QuestionVisibility" SET "Visible" = ? WHERE "CompanyID" = ? AND "QuestionID" = ?', (visible_value, company_id, question_id))

                existing_response = connection.execute('SELECT 1 FROM "Responses" WHERE "CompanyID" = ? AND "QuestionID" = ?', (company_id, question_id)).fetchone()
                if existing_response is None:
                    connection.execute('INSERT INTO "Responses" ("CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime") VALUES (?, ?, ?, ?, ?)', (company_id, question_id, "", "", ""))
            if conn is None:
                connection.commit()

                # !!!
                st.info(f"Runtime rows for company '{company_id}' ensured successfully at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
                
        finally:
            if conn is None:
                connection.close()

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
            self._ensure_company_runtime_rows(company_id, questions=self.rows("Questions"), responses=self.responses_for(company_id), conn=conn)
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
            conn.commit()

            # !!!
            st.info(f"Response for question '{question_id}' saved successfully for company '{company_id}' at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}.")

        self.clear_cache()

        return True

    def refresh_question_visibility(self, company_id: str, responses: dict[str, str] | None = None) -> dict[str, str]:
        current_responses = dict(responses or self.responses_for(company_id))
        with self._connect() as conn:
            self._ensure_company_runtime_rows(company_id, questions=self.rows("Questions"), responses=current_responses, conn=conn)
            conn.commit()
            
            # !!!
            st.info(f"Question visibility refreshed successfully for company '{company_id}'. at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

        self._design_cache = None
        self.clear_cache()
        return current_responses

    def _update_company_last_updated(self, company_id: str, stamp: str, conn: sqlite3.Connection | None = None) -> None:
        connection = conn or self._connect()
        try:
            connection.execute('UPDATE "Companies" SET "LastUpdated" = ? WHERE "CompanyID" = ?', (stamp, company_id))
            if conn is None:
                connection.commit()

                # !!!
                st.info(f"Company '{company_id}' last updated timestamp set to '{stamp}' at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}.")

        finally:
            if conn is None:
                connection.close()

    def append_response_history(self, company_id: str, question_id: str, old_value: str, new_value: str, email: str, stamp: str, conn: sqlite3.Connection | None = None) -> None:
        connection = conn or self._connect()
        try:
            connection.execute('INSERT INTO "ResponseHistory" ("CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime") VALUES (?, ?, ?, ?, ?, ?)', (company_id, question_id, old_value, new_value, email, stamp))
            if conn is None:
                connection.commit()

                # !!!
                st.info(f"Response history for question '{question_id}' appended successfully for company '{company_id}' at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}.")

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
        if not libsql_available():
            raise RuntimeError("TursoRepository requires the 'libsql' package.") from LIBSQL_IMPORT_ERROR
        self.database_url = database_url or ""
        self.auth_token = auth_token or ""
        self._connection: Any | None = None
        self._rows_cache: dict[str, list[dict[str, str]]] = {}
        self._design_cache: dict[str, Any] | None = None
        self._login_cache: dict[str, list[dict[str, str]]] = {}
        self._history_counter = 0
        self._initialize_schema()

    def _disconnect(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def _connect(self) -> Any:
        if self._connection is not None:
            try:
                self._connection.execute("SELECT 1")
                return self._connection
            except BaseException:
                self._disconnect()
        if not self.database_url:
            raise ValueError("Turso database URL is required")
        if not self.auth_token:
            raise ValueError("Turso authentication token is required")
        try:
            self._connection = libsql.connect(self.database_url, auth_token=self.auth_token)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise RuntimeError(f"Failed to connect to Turso: {exc}") from exc
        return self._connection

    def _initialize_schema(self) -> None:
        conn = self._connect()
        for sheet_name in SHEETS:
            headers = SHEET_HEADERS.get(sheet_name, [])
            if not headers:
                continue
            columns = ", ".join(f'"{header}" TEXT' for header in headers)
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{sheet_name}" ({columns})')

        for sheet_name in SHEETS:
            existing = int(conn.execute(f'SELECT COUNT(*) FROM "{sheet_name}"').fetchone()[0])
            if existing:
                continue
            if sheet_name in {"Responses", "ResponseHistory"}:
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
        conn.commit()

        # !!!
        st.info("Database schema initialized successfully in Turso at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}.")

    def _rows_for_sheet(self, worksheet: str) -> list[dict[str, str]]:
        attempt = 0
        while True:
            attempt += 1
            conn = self._connect()
            try:
                cursor = conn.execute(f'SELECT * FROM "{worksheet}"')
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                if attempt == 1:
                    self._disconnect()
                    continue
                raise RuntimeError(f"Failed to query Turso worksheet '{worksheet}': {exc}") from exc
            columns = [column[0] for column in cursor.description or []]
            rows = []
            for row in cursor.fetchall():
                payload = {key: "" if value is None else str(value) for key, value in zip(columns, row)}
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
        self._disconnect()
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
        self._ensure_company_runtime_rows(company_id, questions=self.rows("Questions"), responses=self.responses_for(company_id), conn=conn)
        existing_row = conn.execute('SELECT "ResponseValue" FROM "Responses" WHERE "CompanyID" = ? AND "QuestionID" = ?', (company_id, question_id)).fetchone()
        existing = existing_row[0] if existing_row else None
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
        conn.commit()

        # !!!
        st.info(f"Response for question '{question_id}' saved successfully for company '{company_id}' at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}.")

        self.clear_cache()

        return True

    def refresh_question_visibility(self, company_id: str, responses: dict[str, str] | None = None) -> dict[str, str]:
        current_responses = dict(responses or self.responses_for(company_id))
        conn = self._connect()
        try:
            self._ensure_company_runtime_rows(company_id, questions=self.rows("Questions"), responses=current_responses, conn=conn)
            conn.commit()

            # !!!
            st.info(f"Question visibility refreshed successfully for company '{company_id}' at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}.")

        finally:
            self._disconnect()

        self._design_cache = None
        self.clear_cache()
        return current_responses

    def _update_company_last_updated(self, company_id: str, stamp: str, conn: Any | None = None) -> None:
        connection = conn or self._connect()
        connection.execute('UPDATE "Companies" SET "LastUpdated" = ? WHERE "CompanyID" = ?', (stamp, company_id))

    def append_response_history(self, company_id: str, question_id: str, old_value: str, new_value: str, email: str, stamp: str, conn: Any | None = None) -> None:
        connection = conn or self._connect()
        connection.execute('INSERT INTO "ResponseHistory" ("CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime") VALUES (?, ?, ?, ?, ?, ?)', (company_id, question_id, old_value, new_value, email, stamp))

    def submit(self, company_id: str, email: str) -> None:
        company = self.company(company_id)
        if company is None:
            raise ValueError("Company does not exist")
        stamp = now()
        conn = self._connect()
        conn.execute('UPDATE "Companies" SET "Status" = ?, "LastUpdated" = ?, "SubmittedBy" = ?, "SubmittedTime" = ? WHERE "CompanyID" = ?', ("Submitted", stamp, email, stamp, company_id))
        conn.commit()

        # !!!
        st.info(f"Company '{company_id}' submitted successfully by '{email}' at '{stamp}' at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}.")

        self.clear_cache()


class GoogleSheetsRepository:
    """Minimal Google Sheets-compatible repository used by the tests and local compatibility paths."""

    def __init__(self, spreadsheet: Any) -> None:
        self.spreadsheet = spreadsheet
        self._rows_cache: dict[str, list[dict[str, str]]] = {}
        self._design_cache: dict[str, Any] | None = None
        self._login_cache: dict[str, list[dict[str, str]]] = {}
        self._history_counter = 0

    def _rows_for_sheet(self, worksheet: str) -> list[dict[str, str]]:
        try:
            sheet = self.spreadsheet.worksheet(worksheet)
        except KeyError:
            return []
        rows = sheet.get_all_records(default_blank="") if hasattr(sheet, "get_all_records") else []
        return [{key: "" if value is None else str(value) for key, value in row.items()} for row in rows]

    def _ensure_company_runtime_rows(self, company_id: str, questions: list[dict[str, str]] | None = None, responses: dict[str, str] | None = None, conn: Any | None = None) -> None:
        company_id = str(company_id or "").strip()
        if not company_id:
            return
        if questions is None:
            questions = self.rows("Questions")
        if not questions:
            return

        current_responses = dict(responses or self.responses_for(company_id))
        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        for question in questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            try:
                from .condition_engine import is_question_visible
            except ImportError:  # pragma: no cover - support running module directly
                from condition_engine import is_question_visible
            visible = is_question_visible(question, conditions_by_question.get(question_id, []), current_responses)
            visible_value = "TRUE" if visible else "FALSE"
            existing = next((row for row in self.rows("QuestionVisibility") if row.get("CompanyID") == company_id and row.get("QuestionID") == question_id), None)
            if existing is None:
                self.rows("QuestionVisibility").append({"CompanyID": company_id, "QuestionID": question_id, "Visible": visible_value})
            else:
                existing["Visible"] = visible_value

            existing_response = next((row for row in self.rows("Responses") if row.get("CompanyID") == company_id and row.get("QuestionID") == question_id), None)
            if existing_response is None:
                self.rows("Responses").append({"CompanyID": company_id, "QuestionID": question_id, "ResponseValue": "", "LastModifiedBy": "", "LastModifiedTime": ""})

        self._rows_cache.pop("QuestionVisibility", None)
        self._rows_cache.pop("Responses", None)

    def rows(self, worksheet: str) -> list[dict[str, str]]:
        if worksheet not in self._rows_cache:
            self._rows_cache[worksheet] = deepcopy(self._rows_for_sheet(worksheet))
        return deepcopy(self._rows_cache[worksheet])

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
        if force:
            self._rows_cache.pop("Companies", None)
            self._rows_cache.pop("Users", None)
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
            pages = self.rows("Pages") if "Pages" in self.spreadsheet.worksheets else []
            self._design_cache = {
                "Pages": pages,
                "Questions": questions,
                "OptionsByQuestion": options_by_question,
                "ConditionsByQuestion": conditions_by_question,
            }
        return self._design_cache

    def company(self, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        company_rows = self._login_cache.get("Companies", [])
        if company_rows:
            return next((row for row in company_rows if row["CompanyID"] == company_id), None)
        try:
            worksheet = self.spreadsheet.worksheet("Companies")
            rows = getattr(worksheet, "rows", None)
            if rows is not None:
                return next((row for row in rows if row.get("CompanyID") == company_id), None)
        except KeyError:
            return None
        return None

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
        worksheet = self.spreadsheet.worksheet("Responses")
        rows = getattr(worksheet, "rows", None)
        if rows is not None:
            existing = next((row for row in rows if row.get("CompanyID") == company_id and row.get("QuestionID") == question_id), None)
            old = existing.get("ResponseValue", "") if existing else ""
            if old == value:
                return False
            if existing is not None:
                existing.update({"ResponseValue": value, "LastModifiedBy": email, "LastModifiedTime": now()})
            else:
                rows.append({"CompanyID": company_id, "QuestionID": question_id, "ResponseValue": value, "LastModifiedBy": email, "LastModifiedTime": now()})
        self._update_company_last_updated(company_id, now())
        self.append_response_history(company_id, question_id, old if rows is not None else "", value, email, now())
        self._rows_cache.pop("Companies", None)
        self._login_cache.clear()
        return True

    def refresh_question_visibility(self, company_id: str, responses: dict[str, str] | None = None) -> dict[str, str]:
        current_responses = dict(responses or self.responses_for(company_id))
        self._ensure_company_runtime_rows(company_id, questions=self.rows("Questions"), responses=current_responses)
        self._design_cache = None
        self.clear_cache()
        return current_responses

    def _update_company_last_updated(self, company_id: str, stamp: str) -> None:
        worksheet = self.spreadsheet.worksheet("Companies")
        if hasattr(worksheet, "rows"):
            company_row = next((row for row in worksheet.rows if row.get("CompanyID") == company_id), None)
            if company_row is not None:
                company_row.update({"LastUpdated": stamp, "SubmittedBy": company_row.get("SubmittedBy", ""), "SubmittedTime": company_row.get("SubmittedTime", "")})
                headers = getattr(worksheet, "headers", SHEET_HEADERS["Companies"])
                if hasattr(worksheet, "update"):
                    worksheet.update("A1", [[company_row.get(header, "") for header in headers]])
                self._rows_cache.pop("Companies", None)
                self._login_cache.clear()
                return
        if hasattr(worksheet, "update"):
            worksheet.update("A1", [[stamp]])
        self._rows_cache.pop("Companies", None)
        self._login_cache.clear()

    def append_response_history(self, company_id: str, question_id: str, old_value: str, new_value: str, email: str, stamp: str) -> None:
        self._history_counter += 1
        worksheet = self.spreadsheet.worksheet("ResponseHistory")
        if hasattr(worksheet, "rows"):
            worksheet.rows.append({"HistoryID": f"H{self._history_counter:04}", "CompanyID": company_id, "QuestionID": question_id, "OldValue": old_value, "NewValue": new_value, "ModifiedBy": email, "ModifiedTime": stamp})
        if hasattr(worksheet, "append_row"):
            worksheet.append_row([f"H{self._history_counter:04}", company_id, question_id, old_value, new_value, email, stamp])

    def submit(self, company_id: str, email: str) -> None:
        company = self.company(company_id)
        if company is None:
            raise ValueError("Company does not exist")
        stamp = now()
        worksheet = self.spreadsheet.worksheet("Companies")
        if hasattr(worksheet, "rows"):
            company_row = next((row for row in worksheet.rows if row.get("CompanyID") == company_id), None)
            if company_row is not None:
                company_row.update({"Status": "Submitted", "LastUpdated": stamp, "SubmittedBy": email, "SubmittedTime": stamp})
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

    def _ensure_company_runtime_rows(self, company_id: str, questions: list[dict[str, str]] | None = None, responses: dict[str, str] | None = None) -> None:
        company_id = str(company_id or "").strip()
        if not company_id:
            return
        if questions is None:
            questions = self.rows("Questions")
        if not questions:
            return

        current_responses = dict(responses or self.responses_for(company_id))
        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        self.tables.setdefault("QuestionVisibility", [])
        self.tables.setdefault("Responses", [])

        for question in questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            try:
                from .condition_engine import is_question_visible
            except ImportError:  # pragma: no cover - support running module directly
                from condition_engine import is_question_visible
            visible = is_question_visible(question, conditions_by_question.get(question_id, []), current_responses)
            visible_value = "TRUE" if visible else "FALSE"
            existing = next((row for row in self.tables["QuestionVisibility"] if row.get("CompanyID") == company_id and row.get("QuestionID") == question_id), None)
            if existing is None:
                self.tables["QuestionVisibility"].append({"CompanyID": company_id, "QuestionID": question_id, "Visible": visible_value})
            else:
                existing["Visible"] = visible_value

            existing_response = next((row for row in self.tables["Responses"] if row.get("CompanyID") == company_id and row.get("QuestionID") == question_id), None)
            if existing_response is None:
                self.tables["Responses"].append({"CompanyID": company_id, "QuestionID": question_id, "ResponseValue": "", "LastModifiedBy": "", "LastModifiedTime": ""})

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
        self.clear_cache()
        return True

    def refresh_question_visibility(self, company_id: str, responses: dict[str, str] | None = None) -> dict[str, str]:
        try:
            from .condition_engine import is_question_visible
        except ImportError:  # pragma: no cover - support running module directly
            from condition_engine import is_question_visible

        current_responses = dict(responses or self.responses_for(company_id))
        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        for question in self.tables["Questions"]:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            visible = is_question_visible(question, conditions_by_question.get(question_id, []), current_responses)
            self._ensure_company_runtime_rows(company_id, questions=[question], responses=current_responses)
            self.tables["QuestionVisibility"] = [row for row in self.tables["QuestionVisibility"] if not (row.get("CompanyID") == company_id and row.get("QuestionID") == question_id)]
            self.tables["QuestionVisibility"].append({"CompanyID": company_id, "QuestionID": question_id, "Visible": "TRUE" if visible else "FALSE"})

        self._design_cache = None
        self._static_cache.clear()
        self._login_cache.clear()
        return current_responses

    def append_response_history(self, company_id: str, question_id: str, old_value: str, new_value: str, email: str, stamp: str) -> None:
        self._history_counter += 1
        self.rows("ResponseHistory").append({"HistoryID": f"H{self._history_counter:04}", "CompanyID": company_id, "QuestionID": question_id, "OldValue": old_value, "NewValue": new_value, "ModifiedBy": email, "ModifiedTime": stamp})

    def submit(self, company_id: str, email: str) -> None:
        company = self.company(company_id)
        assert company
        stamp = now()
        company.update({"Status": "Submitted", "LastUpdated": stamp, "SubmittedBy": email, "SubmittedTime": stamp})



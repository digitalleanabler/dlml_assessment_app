"""Repository implementations for local SQLite, Turso, and in-memory use."""

from __future__ import annotations
import sqlite3
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import inspect
import streamlit as st
import sys
from datetime import datetime
try:
    import libsql
except Exception as exc:  # pragma: no cover - optional dependency for cloud mode
    libsql = None  # type: ignore[assignment]
    LIBSQL_IMPORT_ERROR = exc
else:
    LIBSQL_IMPORT_ERROR = None


SHEETS = ("Companies", "Users", "Pages", "Questions", "QuestionOptions", "QuestionConditions", "QuestionVisibility", "Responses", "ResponseHistory")
STATIC_TABLES = {"Pages", "Questions", "QuestionOptions", "QuestionConditions"}
# Step 4.4: tables keyed by (CompanyID, QuestionID) get a real UNIQUE index so a single
# INSERT ... ON CONFLICT (...) DO UPDATE can replace the old "SELECT to check existence,
# then branch to INSERT or UPDATE" pattern used throughout this module.
UPSERT_KEY_TABLES = ("Responses", "QuestionVisibility")
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
SEED: dict[str, list[dict[str, str]]] = {
    "Companies": [
        {"CompanyID": "C001", "CompanyName": "SWAT in Dream", "Status": "Draft", "LastUpdated": "",  "SubmittedBy": "", "SubmittedTime": ""},
        {"CompanyID": "C002", "CompanyName": "LAFD in Dream", "Status": "Draft", "LastUpdated": "", "SubmittedBy": "", "SubmittedTime": ""},
    ],
    "Users": [
        {"Email": "tonytanakorn@gmail.com", "Name": "Tony", "CompanyID": "C001"},
        {"Email": "digitalleanabler@gmail.com", "Name": "Leanabler", "CompanyID": "C001"},
        {"Email": "hondo@gmail.com", "Name": "Hondo", "CompanyID": "C001"},
        {"Email": "deac@gmail.com", "Name": "Deacon", "CompanyID": "C001"},
        {"Email": "tanakorn.tan@nectec.or.th", "Name": "Tanakorn", "CompanyID": "C002"},
        {"Email": "toneiam@gmail.com", "Name": "Eiam", "CompanyID": "C002"},
        {"Email": "bobby@gmail.com", "Name": "Bobby", "CompanyID": "C002"},
        {"Email": "buck@gmail.com", "Name": "Buck", "CompanyID": "C002"},
    ],
    "Pages": [
        {"PageID": "P001", "PageTitle": "???"},
        {"PageID": "P002", "PageTitle": "???"},
        {"PageID": "P003", "PageTitle": "???"},
    ],
    "Questions": [
        {"QuestionID": "Q001", "PageID": "P001", "Sequence": "1", "QuestionText": "???", "AnswerType": "Text", "Required": "TRUE"},
        {"QuestionID": "Q002", "PageID": "P002", "Sequence": "2", "QuestionText": "???", "AnswerType": "Choice", "Required": "TRUE"},
        {"QuestionID": "Q003", "PageID": "P002", "Sequence": "3", "QuestionText": "???", "AnswerType": "Choice", "Required": "TRUE"},
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


def debug(msg):
    frame = inspect.currentframe().f_back
    filename = Path(frame.f_code.co_filename).name
    print(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}: {filename}: {frame.f_lineno}: {frame.f_code.co_name}: {msg}")
    sys.stdout.flush()


def libsql_available() -> bool:
    return libsql is not None


def default_database_path() -> Path:
    return Path(__file__).resolve().parent.parent / "db" / "dlmlassessmentdb.sqlite"


def default_workbook_path() -> Path:
    return default_database_path()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def _multi_row_upsert(
    conn: Any,
    table: str,
    columns: list[str],
    key_columns: tuple[str, str],
    update_columns: list[str],
    rows: list[tuple],
) -> None:
    """Step 4.3: build one INSERT ... VALUES (...), (...), ... ON CONFLICT(...) DO
    UPDATE statement covering every row in `rows`, so writing N changed rows costs one
    round trip instead of N. Relies on the UNIQUE(CompanyID, QuestionID) index added in
    step 4.4 to make ON CONFLICT resolvable. Works against both sqlite3 and libsql
    connections since both accept a flat sequence of '?' parameters."""
    if not rows:
        return
    placeholders = ", ".join("?" for _ in columns)
    values_clause = ", ".join(f"({placeholders})" for _ in rows)
    conflict_cols = ", ".join(f'"{c}"' for c in key_columns)
    set_clause = ", ".join(f'"{c}" = excluded."{c}"' for c in update_columns)
    col_list = ", ".join(f'"{c}"' for c in columns)
    sql = f'INSERT INTO "{table}" ({col_list}) VALUES {values_clause} ON CONFLICT({conflict_cols}) DO UPDATE SET {set_clause}'
    flattened = [value for row in rows for value in row]
    conn.execute(sql, flattened)


def _multi_row_insert(conn: Any, table: str, columns: list[str], rows: list[tuple]) -> None:
    """Step 4.3: plain multi-row INSERT (no conflict target needed - ResponseHistory is
    append-only), so N history rows cost one round trip instead of N."""
    if not rows:
        return
    placeholders = ", ".join("?" for _ in columns)
    values_clause = ", ".join(f"({placeholders})" for _ in rows)
    col_list = ", ".join(f'"{c}"' for c in columns)
    sql = f'INSERT INTO "{table}" ({col_list}) VALUES {values_clause}'
    flattened = [value for row in rows for value in row]
    conn.execute(sql, flattened)


def _multi_row_insert_or_ignore(
    conn: Any,
    table: str,
    columns: list[str],
    key_columns: tuple[str, str],
    rows: list[tuple],
) -> None:
    """Step 4.7: bulk 'insert if absent' - mirrors _multi_row_upsert but takes
    ON CONFLICT ... DO NOTHING, so it never clobbers a ResponseValue that may already
    exist. Used to seed missing placeholder Responses rows for every (company,
    question) pair in one statement instead of one INSERT-if-absent per row."""
    if not rows:
        return
    placeholders = ", ".join("?" for _ in columns)
    values_clause = ", ".join(f"({placeholders})" for _ in rows)
    conflict_cols = ", ".join(f'"{c}"' for c in key_columns)
    col_list = ", ".join(f'"{c}"' for c in columns)
    sql = f'INSERT INTO "{table}" ({col_list}) VALUES {values_clause} ON CONFLICT({conflict_cols}) DO NOTHING'
    flattened = [value for row in rows for value in row]
    conn.execute(sql, flattened)


# Step 4.7: even a single batched statement can outgrow a driver's parameter/row
# limits once companies x questions gets large, so bulk writes are split into
# chunks of this size - still a small constant number of round trips, just not
# literally one, for very large datasets.
_BULK_CHUNK_SIZE = 500
def _chunked(rows: list[tuple], size: int = _BULK_CHUNK_SIZE) -> list[list[tuple]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


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

    def _ensure_unique_indexes(self, conn: Any) -> None:
        """Step 4.4: give Responses and QuestionVisibility a real UNIQUE(CompanyID,
        QuestionID) key. SQLite/libsql can't ALTER TABLE to add a constraint after the
        fact, but a UNIQUE index is equivalent for our purposes and *can* be added to an
        existing table. Before adding it, defensively collapse any duplicate
        (CompanyID, QuestionID) rows a prior SELECT-then-branch race might have left
        behind (keeping the highest-rowid, i.e. most recent, copy), otherwise the
        CREATE UNIQUE INDEX would fail on pre-existing data."""
        for table in UPSERT_KEY_TABLES:
            conn.execute(
                f'DELETE FROM "{table}" WHERE rowid NOT IN ('
                f'  SELECT MAX(rowid) FROM "{table}" GROUP BY "CompanyID", "QuestionID"'
                f')'
            )
            conn.execute(
                f'CREATE UNIQUE INDEX IF NOT EXISTS "idx_{table.lower()}_company_question" '
                f'ON "{table}" ("CompanyID", "QuestionID")'
            )
            debug(f"Ensured UNIQUE(CompanyID, QuestionID) index on '{table}'.")

    def ping(self) -> None:
        """Cheap, uncached connectivity check - unlike design_data(), which only
        does real I/O on its first call and returns the cached dict on every
        subsequent call. Used by ensure_repository() to verify the DB is actually
        reachable on every rerun, not just the first one this process."""
        with self._connect() as conn:
            conn.execute("SELECT 1")
            debug(f"Pinged database at {self.database_path} successfully.")

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            for sheet_name in SHEETS:
                headers = SHEET_HEADERS.get(sheet_name, [])
                if not headers:
                    continue
                columns = ", ".join(f'"{header}" TEXT' for header in headers)
                conn.execute(f'CREATE TABLE IF NOT EXISTS "{sheet_name}" ({columns})')
                debug(f"Ensured table '{sheet_name}' exists with columns: {headers}.")

            self._ensure_unique_indexes(conn)

            for sheet_name in SHEETS:
                existing = conn.execute(f'SELECT COUNT(*) FROM "{sheet_name}"').fetchone()[0]
                debug(f"Checked existing rows in {sheet_name}: {existing}.")

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
                    debug(f"Inserted seed data into {sheet_name}: {values}.")

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

                # Step 4.4: one upsert instead of SELECT-to-check-existence + branch.
                # The UNIQUE(CompanyID, QuestionID) index (see _ensure_unique_indexes)
                # is what makes ON CONFLICT resolvable here.
                connection.execute(
                    'INSERT INTO "QuestionVisibility" ("CompanyID", "QuestionID", "Visible") '
                    'VALUES (?, ?, ?) '
                    'ON CONFLICT("CompanyID", "QuestionID") DO UPDATE SET "Visible" = excluded."Visible"',
                    (company_id, question_id, visible_value),
                )
                debug(f"Upserted visibility for question '{question_id}' for company '{company_id}'.")

                # Only ensures a placeholder response row exists (blank value); an
                # existing row's ResponseValue must never be clobbered here, so this
                # stays an insert-if-absent (ON CONFLICT DO NOTHING) rather than a
                # value-overwriting upsert.
                connection.execute(
                    'INSERT INTO "Responses" ("CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime") '
                    'VALUES (?, ?, ?, ?, ?) '
                    'ON CONFLICT("CompanyID", "QuestionID") DO NOTHING',
                    (company_id, question_id, "", "", ""),
                )
                debug(f"Ensured response row exists for question '{question_id}' for company '{company_id}'.")

            if conn is None:
                connection.commit()
                debug(f"Runtime rows for company '{company_id}' ensured successfully at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

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
            debug(f"Updated '{header}' in '{worksheet}' at row {row_number} with value '{value}'.")

    def company(self, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Companies"] if row["CompanyID"] == company_id), None)

    def company_by_name(self, company_name: str) -> dict[str, str] | None:
        self.load_login_data()
        target = company_name.strip().casefold()
        return next((row for row in self._login_cache["Companies"] if row["CompanyName"].strip().casefold() == target), None)

    def company_status(self, company_id: str) -> dict[str, str] | None:
        """Narrow, WHERE-scoped read of just the fields the post-login loop needs
        (Status/SubmittedBy/SubmittedTime) so we don't have to reload the whole
        Companies+Users session tier on every rerun. See design step 4.8."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT "CompanyID", "Status", "LastUpdated", "SubmittedBy", "SubmittedTime" FROM "Companies" WHERE "CompanyID" = ?',
                (company_id,),
            ).fetchone()
        if row is None:
            return None
        return {key: "" if value is None else str(value) for key, value in dict(row).items()}

    def invalidate_static(self) -> None:
        """Drop only the app-level tier (Pages/Questions/QuestionOptions/QuestionConditions).
        Should only be called on an explicit schema/design refresh, not on every rerun."""
        for sheet_name in STATIC_TABLES:
            self._rows_cache.pop(sheet_name, None)
        self._design_cache = None

    def invalidate_session(self) -> None:
        """Drop only the session-level tier (Companies/Users)."""
        self._login_cache.clear()

    def invalidate_page_cache(self, company_id: str) -> None:
        """Placeholder for page-level (Responses/QuestionVisibility) cache invalidation.
        These aren't cached in-process today (rows() re-reads them every call for any
        non-STATIC_TABLES sheet), so there's nothing to drop yet. Reserved for step 4.6
        once per-company response/visibility caching is introduced."""
        return None

    def user(self, email: str, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Users"] if row["Email"].lower() == email.lower() and row["CompanyID"] == company_id), None)

    def responses_for(self, company_id: str) -> dict[str, str]:
        # Scoped read (design step 4.5): pull only this company's rows instead of
        # SELECT * FROM "Responses" filtered in Python.
        with self._connect() as conn:
            cursor = conn.execute('SELECT "QuestionID", "ResponseValue" FROM "Responses" WHERE "CompanyID" = ?', (company_id,))
            return {row["QuestionID"]: ("" if row["ResponseValue"] is None else str(row["ResponseValue"])) for row in cursor.fetchall()}

    def visibility_for(self, company_id: str) -> dict[str, str]:
        # Scoped read, mirrors responses_for. Used by save_responses (step 4.3) to
        # diff against the last-persisted visibility snapshot.
        with self._connect() as conn:
            cursor = conn.execute('SELECT "QuestionID", "Visible" FROM "QuestionVisibility" WHERE "CompanyID" = ?', (company_id,))
            return {row["QuestionID"]: ("" if row["Visible"] is None else str(row["Visible"])) for row in cursor.fetchall()}

    def save_responses(self, company_id: str, changes: dict[str, str], email: str) -> int:
        """Step 4.3: batched replacement for calling save_response() once per changed
        answer (each of which ran a full O(questions) visibility recompute via
        _ensure_company_runtime_rows) followed by a second, redundant, company-wide
        refresh_question_visibility() pass. Instead: load current responses/visibility
        once, recompute the whole company's visibility map in memory (pure, no I/O),
        diff it against what's stored, and write only what actually changed - one
        batched statement per table, regardless of how many answers changed or how
        many questions the company has. Returns the count of responses whose value
        actually changed.

        ADR-007 note: `changes` may now (as of app.py's PageSession.save_to_repository)
        contain every question on the page being saved, not only ones the caller
        edited - "Save-what-you-see." The `actual_changes` diff below is kept exactly
        as-is on purpose: it is a write-avoidance optimization (skip a no-op UPDATE /
        ResponseHistory row when the incoming value already matches the database),
        not a filter on caller intent. Do not repurpose it back into an intent filter
        (e.g. by having the caller only send fields it deems "changed") - that's the
        behavior ADR-007 replaced. Do not remove it either - that would make every
        save write every field on the page unconditionally, scaling DB writes with
        page size instead of with actual changes."""
        company_id = str(company_id or "").strip()
        if not company_id or not changes:
            return 0

        current_responses = self.responses_for(company_id)
        actual_changes = {
            str(question_id): str(value)
            for question_id, value in changes.items()
            if str(current_responses.get(str(question_id), "")) != str(value)
        }
        if not actual_changes:
            return 0

        merged_responses = dict(current_responses)
        merged_responses.update(actual_changes)

        try:
            from .condition_engine import is_question_visible
        except ImportError:  # pragma: no cover - support running module directly
            from condition_engine import is_question_visible

        all_questions = self.rows("Questions")
        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        current_visibility = self.visibility_for(company_id)
        visibility_diff: dict[str, str] = {}
        for question in all_questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            visible = is_question_visible(question, conditions_by_question.get(question_id, []), merged_responses)
            visible_value = "TRUE" if visible else "FALSE"
            if current_visibility.get(question_id) != visible_value:
                visibility_diff[question_id] = visible_value

        stamp = now()
        with self._connect() as conn:
            response_rows = [(company_id, question_id, value, email, stamp) for question_id, value in actual_changes.items()]
            _multi_row_upsert(
                conn, "Responses",
                columns=["CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime"],
                key_columns=("CompanyID", "QuestionID"),
                update_columns=["ResponseValue", "LastModifiedBy", "LastModifiedTime"],
                rows=response_rows,
            )
            debug(f"Batched upsert of {len(response_rows)} response(s) for company '{company_id}'.")

            if visibility_diff:
                visibility_rows = [(company_id, question_id, value) for question_id, value in visibility_diff.items()]
                _multi_row_upsert(
                    conn, "QuestionVisibility",
                    columns=["CompanyID", "QuestionID", "Visible"],
                    key_columns=("CompanyID", "QuestionID"),
                    update_columns=["Visible"],
                    rows=visibility_rows,
                )
                debug(f"Batched upsert of {len(visibility_rows)} visibility row(s) for company '{company_id}'.")

            history_rows = [
                (company_id, question_id, current_responses.get(question_id, ""), value, email, stamp)
                for question_id, value in actual_changes.items()
            ]
            _multi_row_insert(
                conn, "ResponseHistory",
                columns=["CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime"],
                rows=history_rows,
            )
            debug(f"Batched insert of {len(history_rows)} history row(s) for company '{company_id}'.")

            self._update_company_last_updated(company_id, stamp, conn=conn)
            conn.commit()
            debug(f"save_responses committed {len(actual_changes)} changed response(s) for company '{company_id}'.")

        # No clear_cache() here: Responses/QuestionVisibility were never in the cached
        # STATIC_TABLES tier to begin with, so there's nothing to invalidate - and not
        # calling it also stops re-triggering root cause 2.1 (static/session tiers being
        # wiped) on the very next rerun after a save.
        return len(actual_changes)

    def save_response(self, company_id: str, question_id: str, value: str, email: str) -> bool:
        """LEGACY / NOT CALLED BY app.py. Kept only for interface parity and any
        external callers (e.g. scripts, tests) that still target the single-answer
        API. app.py's live save path always goes through the batched
        save_responses(), which computes one company-wide visibility diff and issues
        one upsert per table regardless of how many answers changed. This method
        still has the old O(1 question) round-trip shape *per call* via
        _ensure_company_runtime_rows() - calling it once per changed answer
        reintroduces the O(changed_answers x total_questions) fan-out the batched
        path was built to eliminate (see code_analysis_r03, section 2.5). Do not
        wire this into any per-answer save loop; use save_responses() instead."""
        with self._connect() as conn:
            self._ensure_company_runtime_rows(company_id, questions=self.rows("Questions"), responses=self.responses_for(company_id), conn=conn)
            existing = conn.execute('SELECT "ResponseValue" FROM "Responses" WHERE "CompanyID" = ? AND "QuestionID" = ?', (company_id, question_id)).fetchone()
            debug(f"Attempting to save response for question '{question_id}' for company '{company_id}'.")
            old = existing[0] if existing else ""
            if old == value:
                return False
            stamp = now()
            # Step 4.4: the prior SELECT above is still needed (to get `old` for the
            # change check and history row), but the write itself is now one upsert
            # instead of branching between UPDATE and INSERT.
            conn.execute(
                'INSERT INTO "Responses" ("CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime") '
                'VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT("CompanyID", "QuestionID") DO UPDATE SET '
                '"ResponseValue" = excluded."ResponseValue", '
                '"LastModifiedBy" = excluded."LastModifiedBy", '
                '"LastModifiedTime" = excluded."LastModifiedTime"',
                (company_id, question_id, value, email, stamp),
            )
            debug(f"Upserted response for question '{question_id}' for company '{company_id}'.")
            self._update_company_last_updated(company_id, stamp, conn=conn)
            self.append_response_history(company_id, question_id, old, value, email, stamp, conn=conn)
            conn.commit()
            debug(f"Response for question '{question_id}' saved successfully for company '{company_id}'.")
        self.clear_cache()
        return True

    def refresh_question_visibility(self, company_id: str, responses: dict[str, str] | None = None) -> dict[str, str]:
        """LEGACY / NOT CALLED BY app.py. Kept for interface parity only. Visibility
        is computed purely in memory during rendering/readiness checks
        (PageSession.recompute_visibility in app.py, per code_analysis_r03 section
        4.2) and QuestionVisibility is written as a batched diff inside
        save_responses(). This method still round-trips through
        _ensure_company_runtime_rows() per question and also calls the broad
        self.clear_cache() (wiping static/session tiers too), so calling it from any
        new code path would reintroduce both the N+1 fan-out and the over-broad
        cache invalidation the redesign eliminated. Prefer initialize_all_visibility()
        for bulk/cold-start needs and save_responses() for per-save needs."""
        current_responses = dict(responses or self.responses_for(company_id))
        with self._connect() as conn:
            self._ensure_company_runtime_rows(company_id, questions=self.rows("Questions"), responses=current_responses, conn=conn)
            conn.commit()
            debug(f"Question visibility refreshed successfully for company '{company_id}'.")
        self._design_cache = None
        self.clear_cache()
        # Record which companies were seeded by this bulk operation so we
        # don't re-seed them again per-process on first login.
        try:
            self._visibility_initialized_companies = {str(c.get("CompanyID", "") or "").strip() for c in companies if str(c.get("CompanyID", "") or "").strip()}
        except Exception:
            # Defensive: never let this bookkeeping break initialization.
            pass

    def initialize_company_visibility(self, company_id: str) -> None:
        """Scoped, batched visibility initialization for a single company.
        Mirrors initialize_all_visibility() but confines reads/writes to one
        company and uses chunked batched statements to avoid N+1 round trips.
        """
        company_id = str(company_id or "").strip()
        if not company_id:
            return

        questions = self.rows("Questions")
        if not questions:
            return

        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        try:
            from .condition_engine import is_question_visible
        except ImportError:  # pragma: no cover - support running module directly
            from condition_engine import is_question_visible

        current_responses = self.responses_for(company_id)
        visibility_rows: list[tuple] = []
        response_seed_rows: list[tuple] = []

        for question in questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            visible = is_question_visible(question, conditions_by_question.get(question_id, []), current_responses)
            visibility_rows.append((company_id, question_id, "TRUE" if visible else "FALSE"))
            if question_id not in current_responses:
                response_seed_rows.append((company_id, question_id, "", "", ""))

        with self._connect() as conn:
            for chunk in _chunked(visibility_rows):
                _multi_row_upsert(
                    conn, "QuestionVisibility",
                    columns=["CompanyID", "QuestionID", "Visible"],
                    key_columns=("CompanyID", "QuestionID"),
                    update_columns=["Visible"],
                    rows=chunk,
                )
            for chunk in _chunked(response_seed_rows):
                _multi_row_insert_or_ignore(
                    conn, "Responses",
                    columns=["CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime"],
                    key_columns=("CompanyID", "QuestionID"),
                    rows=chunk,
                )
            conn.commit()

        # Mark this company as seeded for this repo instance.
        seeded = getattr(self, "_visibility_initialized_companies", None)
        if seeded is None:
            seeded = set()
        seeded.add(company_id)
        self._visibility_initialized_companies = seeded
        # Keep design cache None so subsequent reads pick up any changes.
        self._design_cache = None
        return current_responses

    def initialize_all_visibility(self) -> None:
        """Step 4.7: cold-start visibility bootstrap for every company. The old path
        (initialize_host_visibility calling refresh_question_visibility per company,
        which itself calls _ensure_company_runtime_rows) cost one SELECT + up to two
        writes PER QUESTION PER COMPANY - a companies x questions round-trip fan-out
        at process start. Here the whole thing is: one read of the entire Responses
        table (cheaper than N per-company scoped reads when every company needs
        reading anyway), one in-memory visibility computation per (company, question)
        pair (pure, no I/O - condition_engine.is_question_visible), then a small,
        constant number of batched upsert/insert statements covering every row,
        chunked only to stay under driver parameter limits for very large datasets."""
        companies = self.rows("Companies")
        questions = self.rows("Questions")
        if not companies or not questions:
            return

        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        try:
            from .condition_engine import is_question_visible
        except ImportError:  # pragma: no cover - support running module directly
            from condition_engine import is_question_visible

        # One full read of Responses instead of one scoped read per company: for a
        # one-time bulk pass across *every* company, this is cheaper than N round
        # trips (see 4.5's rationale for why per-company reads are scoped elsewhere -
        # that logic doesn't apply when every company needs reading anyway).
        responses_by_company: dict[str, dict[str, str]] = {}
        with self._connect() as conn:
            for row in conn.execute('SELECT "CompanyID", "QuestionID", "ResponseValue" FROM "Responses"').fetchall():
                payload = dict(row)
                responses_by_company.setdefault(payload["CompanyID"], {})[payload["QuestionID"]] = payload["ResponseValue"] or ""

        visibility_rows: list[tuple] = []
        response_seed_rows: list[tuple] = []
        for company in companies:
            company_id = str(company.get("CompanyID", "") or "").strip()
            if not company_id:
                continue
            current_responses = responses_by_company.get(company_id, {})
            for question in questions:
                question_id = str(question.get("QuestionID", "") or "").strip()
                if not question_id:
                    continue
                visible = is_question_visible(question, conditions_by_question.get(question_id, []), current_responses)
                visibility_rows.append((company_id, question_id, "TRUE" if visible else "FALSE"))
                if question_id not in current_responses:
                    response_seed_rows.append((company_id, question_id, "", "", ""))

        with self._connect() as conn:
            for chunk in _chunked(visibility_rows):
                _multi_row_upsert(
                    conn, "QuestionVisibility",
                    columns=["CompanyID", "QuestionID", "Visible"],
                    key_columns=("CompanyID", "QuestionID"),
                    update_columns=["Visible"],
                    rows=chunk,
                )
            for chunk in _chunked(response_seed_rows):
                _multi_row_insert_or_ignore(
                    conn, "Responses",
                    columns=["CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime"],
                    key_columns=("CompanyID", "QuestionID"),
                    rows=chunk,
                )
            conn.commit()
            debug(f"Bulk-initialized visibility for {len(companies)} companies x {len(questions)} questions in {len(_chunked(visibility_rows)) + len(_chunked(response_seed_rows))} batched statement(s).")

        self._design_cache = None
        self.clear_cache()

    def _update_company_last_updated(self, company_id: str, stamp: str, conn: sqlite3.Connection | None = None) -> None:
        connection = conn or self._connect()
        try:
            connection.execute('UPDATE "Companies" SET "LastUpdated" = ? WHERE "CompanyID" = ?', (stamp, company_id))
            debug(f"Company '{company_id}' last updated timestamp set to '{stamp}'.")
            if conn is None:
                connection.commit()
                debug(f"Company '{company_id}' last updated timestamp set to '{stamp}'.")
        finally:
            if conn is None:
                connection.close()

    def append_response_history(self, company_id: str, question_id: str, old_value: str, new_value: str, email: str, stamp: str, conn: sqlite3.Connection | None = None) -> None:
        connection = conn or self._connect()
        try:
            connection.execute('INSERT INTO "ResponseHistory" ("CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime") VALUES (?, ?, ?, ?, ?, ?)', (company_id, question_id, old_value, new_value, email, stamp))
            if conn is None:
                connection.commit()
                debug(f"Response history for question '{question_id}' appended successfully for company '{company_id}'.")
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
            debug(f"Company '{company_id}' submitted successfully by '{email}' at '{stamp}'.") 
        self.clear_cache()


class TursoRepository(SQLiteRepository):
    """Repository backed by a remote Turso database for cloud deployment."""

    def __init__(self, database_url: str | None = None, auth_token: str | None = None) -> None:
        if not libsql_available():
            raise RuntimeError("TursoRepository requires the 'libsql' package.") from LIBSQL_IMPORT_ERROR
        self.database_url = database_url or ""
        self.auth_token = auth_token or ""
        # Thread-local storage: get_repository() is cached process-wide via
        # @st.cache_resource, so this single TursoRepository instance is shared
        # across every concurrent Streamlit session thread. libsql connections
        # aren't safe to share across threads, so each thread gets its own
        # connection instead of racing on a single self._connection attribute.
        self._local = threading.local()
        self._rows_cache: dict[str, list[dict[str, str]]] = {}
        self._design_cache: dict[str, Any] | None = None
        self._login_cache: dict[str, list[dict[str, str]]] = {}
        self._history_counter = 0
        self._initialize_schema()

    def _disconnect(self) -> None:
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.connection = None

    def _connect(self) -> Any:
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                debug(f"Reusing existing Turso connection for thread {threading.get_ident()}.")
                return conn
            except BaseException:
                self._disconnect()
        if not self.database_url:
            raise ValueError("Turso database URL is required")
        if not self.auth_token:
            raise ValueError("Turso authentication token is required")
        try:
            conn = libsql.connect(self.database_url, auth_token=self.auth_token)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise RuntimeError(f"Failed to connect to Turso: {exc}") from exc
        self._local.connection = conn
        return conn

    def ping(self) -> None:
        """Override rather than inherit SQLiteRepository.ping(): Turso connections
        are used directly (conn = self._connect()), never via a `with` block, as
        seen throughout the rest of this class - libsql connections aren't
        guaranteed to behave as context managers the same way sqlite3's does."""
        conn = self._connect()
        conn.execute("SELECT 1")

    def _initialize_schema(self) -> None:
        conn = self._connect()
        for sheet_name in SHEETS:
            headers = SHEET_HEADERS.get(sheet_name, [])
            if not headers:
                continue
            columns = ", ".join(f'"{header}" TEXT' for header in headers)
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{sheet_name}" ({columns})')
            debug(f"Ensured table '{sheet_name}' exists with columns: {headers}.")
        self._ensure_unique_indexes(conn)

        for sheet_name in SHEETS:
            existing = int(conn.execute(f'SELECT COUNT(*) FROM "{sheet_name}"').fetchone()[0])
            debug(f"Found {existing} existing rows in '{sheet_name}'.")
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
                debug(f"Inserted seed data into '{sheet_name}'.")
        conn.commit()
        debug(f"Database schema initialized successfully in Turso.")


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

    def company_status(self, company_id: str) -> dict[str, str] | None:
        """Narrow, WHERE-scoped read of just Status/SubmittedBy/SubmittedTime. On Turso
        this is the difference between one small round trip and reloading the entire
        Companies+Users session tier over the network on every rerun. See step 4.8."""
        conn = self._connect()
        cursor = conn.execute(
            'SELECT "CompanyID", "Status", "LastUpdated", "SubmittedBy", "SubmittedTime" FROM "Companies" WHERE "CompanyID" = ?',
            (company_id,),
        )
        columns = [column[0] for column in cursor.description or []]
        row = cursor.fetchone()
        if row is None:
            return None
        return {key: "" if value is None else str(value) for key, value in zip(columns, row)}

    def invalidate_static(self) -> None:
        for sheet_name in STATIC_TABLES:
            self._rows_cache.pop(sheet_name, None)
        self._design_cache = None

    def invalidate_session(self) -> None:
        self._login_cache.clear()

    def invalidate_page_cache(self, company_id: str) -> None:
        return None

    def user(self, email: str, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Users"] if row["Email"].lower() == email.lower() and row["CompanyID"] == company_id), None)

    def responses_for(self, company_id: str) -> dict[str, str]:
        # Scoped read (design step 4.5): only this company's rows travel over the
        # network, instead of every company's Responses row being fetched and then
        # filtered client-side.
        conn = self._connect()
        cursor = conn.execute('SELECT "QuestionID", "ResponseValue" FROM "Responses" WHERE "CompanyID" = ?', (company_id,))
        columns = [column[0] for column in cursor.description or []]
        result: dict[str, str] = {}
        for row in cursor.fetchall():
            payload = dict(zip(columns, row))
            result[payload["QuestionID"]] = "" if payload["ResponseValue"] is None else str(payload["ResponseValue"])
        return result

    def visibility_for(self, company_id: str) -> dict[str, str]:
        # Scoped read, mirrors responses_for. Used by save_responses (step 4.3).
        conn = self._connect()
        cursor = conn.execute('SELECT "QuestionID", "Visible" FROM "QuestionVisibility" WHERE "CompanyID" = ?', (company_id,))
        columns = [column[0] for column in cursor.description or []]
        result: dict[str, str] = {}
        for row in cursor.fetchall():
            payload = dict(zip(columns, row))
            result[payload["QuestionID"]] = "" if payload["Visible"] is None else str(payload["Visible"])
        return result

    def save_responses(self, company_id: str, changes: dict[str, str], email: str) -> int:
        """Step 4.3: see SQLiteRepository.save_responses for the full rationale. On
        Turso this is where it matters most - collapsing what used to be
        P changed answers x N total questions x (2-4 network round trips) down to a
        small constant number of scoped reads plus one batched write per table."""
        company_id = str(company_id or "").strip()
        if not company_id or not changes:
            return 0

        current_responses = self.responses_for(company_id)
        actual_changes = {
            str(question_id): str(value)
            for question_id, value in changes.items()
            if str(current_responses.get(str(question_id), "")) != str(value)
        }
        if not actual_changes:
            return 0

        merged_responses = dict(current_responses)
        merged_responses.update(actual_changes)

        try:
            from .condition_engine import is_question_visible
        except ImportError:  # pragma: no cover - support running module directly
            from condition_engine import is_question_visible

        all_questions = self.rows("Questions")
        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        current_visibility = self.visibility_for(company_id)
        visibility_diff: dict[str, str] = {}
        for question in all_questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            visible = is_question_visible(question, conditions_by_question.get(question_id, []), merged_responses)
            visible_value = "TRUE" if visible else "FALSE"
            if current_visibility.get(question_id) != visible_value:
                visibility_diff[question_id] = visible_value

        stamp = now()
        conn = self._connect()

        response_rows = [(company_id, question_id, value, email, stamp) for question_id, value in actual_changes.items()]
        _multi_row_upsert(
            conn, "Responses",
            columns=["CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime"],
            key_columns=("CompanyID", "QuestionID"),
            update_columns=["ResponseValue", "LastModifiedBy", "LastModifiedTime"],
            rows=response_rows,
        )
        debug(f"Batched upsert of {len(response_rows)} response(s) for company '{company_id}'.")

        if visibility_diff:
            visibility_rows = [(company_id, question_id, value) for question_id, value in visibility_diff.items()]
            _multi_row_upsert(
                conn, "QuestionVisibility",
                columns=["CompanyID", "QuestionID", "Visible"],
                key_columns=("CompanyID", "QuestionID"),
                update_columns=["Visible"],
                rows=visibility_rows,
            )
            debug(f"Batched upsert of {len(visibility_rows)} visibility row(s) for company '{company_id}'.")

        history_rows = [
            (company_id, question_id, current_responses.get(question_id, ""), value, email, stamp)
            for question_id, value in actual_changes.items()
        ]
        _multi_row_insert(
            conn, "ResponseHistory",
            columns=["CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime"],
            rows=history_rows,
        )
        debug(f"Batched insert of {len(history_rows)} history row(s) for company '{company_id}'.")
        self._update_company_last_updated(company_id, stamp, conn=conn)
        conn.commit()
        debug(f"save_responses committed {len(actual_changes)} changed response(s) for company '{company_id}'.")

        # No clear_cache()/disconnect here: keeps the persistent Turso connection alive
        # for reuse instead of forcing a reconnect on the very next call this rerun.
        return len(actual_changes)

    def save_response(self, company_id: str, question_id: str, value: str, email: str) -> bool:
        """LEGACY / NOT CALLED BY app.py. Kept only for interface parity and any
        external callers (e.g. scripts, tests) that still target the single-answer
        API. app.py's live save path always goes through the batched
        save_responses(), which computes one company-wide visibility diff and issues
        one upsert per table regardless of how many answers changed. This method
        still round-trips through _ensure_company_runtime_rows() per question - on
        Turso specifically, that means every one of those round trips pays network
        latency, so calling this once per changed answer reintroduces the
        O(changed_answers x total_questions) network fan-out the batched path was
        built to eliminate (see code_analysis_r03, section 2.5). Do not wire this
        into any per-answer save loop; use save_responses() instead."""
        conn = self._connect()
        self._ensure_company_runtime_rows(company_id, questions=self.rows("Questions"), responses=self.responses_for(company_id), conn=conn)
        existing_row = conn.execute('SELECT "ResponseValue" FROM "Responses" WHERE "CompanyID" = ? AND "QuestionID" = ?', (company_id, question_id)).fetchone()
        debug(f"Attempting to save response for question '{question_id}' for company '{company_id}'.")

        existing = existing_row[0] if existing_row else None
        old = existing or ""
        if old == value:
            return False
        stamp = now()
        # Step 4.4: one upsert over the network instead of a conditional UPDATE/INSERT
        # round trip. On Turso this also collapses what used to be a branch decision
        # into a single statement, which matters more than on local SQLite because
        # every round trip here pays network latency.
        conn.execute(
            'INSERT INTO "Responses" ("CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime") '
            'VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT("CompanyID", "QuestionID") DO UPDATE SET '
            '"ResponseValue" = excluded."ResponseValue", '
            '"LastModifiedBy" = excluded."LastModifiedBy", '
            '"LastModifiedTime" = excluded."LastModifiedTime"',
            (company_id, question_id, value, email, stamp),
        )
        debug(f"Upserted response for question '{question_id}' for company '{company_id}'.")
        self._update_company_last_updated(company_id, stamp, conn=conn)
        self.append_response_history(company_id, question_id, old, value, email, stamp, conn=conn)
        conn.commit()
        debug(f"Response for question '{question_id}' saved successfully for company '{company_id}'.")
        self.clear_cache()

        return True

    def refresh_question_visibility(self, company_id: str, responses: dict[str, str] | None = None) -> dict[str, str]:
        """LEGACY / NOT CALLED BY app.py. Kept for interface parity only. Visibility
        is computed purely in memory during rendering/readiness checks
        (PageSession.recompute_visibility in app.py, per code_analysis_r03 section
        4.2) and QuestionVisibility is written as a batched diff inside
        save_responses(). This method still round-trips through
        _ensure_company_runtime_rows() per question (each one a network round trip
        on Turso) and also calls the broad self.clear_cache() (wiping static/session
        tiers too), so calling it from any new code path would reintroduce both the
        N+1 network fan-out and the over-broad cache invalidation the redesign
        eliminated. Prefer initialize_all_visibility() for bulk/cold-start needs and
        save_responses() for per-save needs."""
        current_responses = dict(responses or self.responses_for(company_id))
        conn = self._connect()
        try:
            self._ensure_company_runtime_rows(company_id, questions=self.rows("Questions"), responses=current_responses, conn=conn)
            conn.commit()
            debug(f"Question visibility refreshed successfully for company '{company_id}'.")
        finally:
            self._disconnect()

        self._design_cache = None
        self.clear_cache()
        return current_responses

    def initialize_all_visibility(self) -> None:
        """Step 4.7: same rationale as SQLiteRepository.initialize_all_visibility -
        one full Responses read, an in-memory visibility computation per (company,
        question), then a small constant number of batched statements. This matters
        even more here than on local SQLite, since every one of the old per-question
        round trips paid Turso's network latency instead of local disk I/O."""
        companies = self.rows("Companies")
        questions = self.rows("Questions")
        if not companies or not questions:
            return

        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        try:
            from .condition_engine import is_question_visible
        except ImportError:  # pragma: no cover - support running module directly
            from condition_engine import is_question_visible

        conn = self._connect()
        responses_by_company: dict[str, dict[str, str]] = {}
        cursor = conn.execute('SELECT "CompanyID", "QuestionID", "ResponseValue" FROM "Responses"')
        columns = [column[0] for column in cursor.description or []]
        for row in cursor.fetchall():
            payload = dict(zip(columns, row))
            responses_by_company.setdefault(payload["CompanyID"], {})[payload["QuestionID"]] = payload["ResponseValue"] or ""

        visibility_rows: list[tuple] = []
        response_seed_rows: list[tuple] = []
        for company in companies:
            company_id = str(company.get("CompanyID", "") or "").strip()
            if not company_id:
                continue
            current_responses = responses_by_company.get(company_id, {})
            for question in questions:
                question_id = str(question.get("QuestionID", "") or "").strip()
                if not question_id:
                    continue
                visible = is_question_visible(question, conditions_by_question.get(question_id, []), current_responses)
                visibility_rows.append((company_id, question_id, "TRUE" if visible else "FALSE"))
                if question_id not in current_responses:
                    response_seed_rows.append((company_id, question_id, "", "", ""))

        for chunk in _chunked(visibility_rows):
            _multi_row_upsert(
                conn, "QuestionVisibility",
                columns=["CompanyID", "QuestionID", "Visible"],
                key_columns=("CompanyID", "QuestionID"),
                update_columns=["Visible"],
                rows=chunk,
            )
        for chunk in _chunked(response_seed_rows):
            _multi_row_insert_or_ignore(
                conn, "Responses",
                columns=["CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime"],
                key_columns=("CompanyID", "QuestionID"),
                rows=chunk,
            )
        conn.commit()
        debug(f"Bulk-initialized visibility for {len(companies)} companies x {len(questions)} questions in {len(_chunked(visibility_rows)) + len(_chunked(response_seed_rows))} batched statement(s).")
        self._design_cache = None
        self.clear_cache()
        # Record seeded companies for this repo instance
        try:
            self._visibility_initialized_companies = {str(c.get("CompanyID", "") or "").strip() for c in companies if str(c.get("CompanyID", "") or "").strip()}
        except Exception:
            pass

    def initialize_company_visibility(self, company_id: str) -> None:
        """Scoped, batched visibility initialization for a single company (Turso).
        Uses the Turso connection style (non-context manager) but mirrors the
        SQLiteRepository implementation to avoid N+1 network round trips.
        """
        company_id = str(company_id or "").strip()
        if not company_id:
            return

        questions = self.rows("Questions")
        if not questions:
            return

        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        try:
            from .condition_engine import is_question_visible
        except ImportError:
            from condition_engine import is_question_visible

        current_responses = self.responses_for(company_id)
        visibility_rows: list[tuple] = []
        response_seed_rows: list[tuple] = []

        for question in questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            visible = is_question_visible(question, conditions_by_question.get(question_id, []), current_responses)
            visibility_rows.append((company_id, question_id, "TRUE" if visible else "FALSE"))
            if question_id not in current_responses:
                response_seed_rows.append((company_id, question_id, "", "", ""))

        conn = self._connect()
        for chunk in _chunked(visibility_rows):
            _multi_row_upsert(
                conn, "QuestionVisibility",
                columns=["CompanyID", "QuestionID", "Visible"],
                key_columns=("CompanyID", "QuestionID"),
                update_columns=["Visible"],
                rows=chunk,
            )
        for chunk in _chunked(response_seed_rows):
            _multi_row_insert_or_ignore(
                conn, "Responses",
                columns=["CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime"],
                key_columns=("CompanyID", "QuestionID"),
                rows=chunk,
            )
        conn.commit()

        seeded = getattr(self, "_visibility_initialized_companies", None)
        if seeded is None:
            seeded = set()
        seeded.add(company_id)
        self._visibility_initialized_companies = seeded
        self._design_cache = None

    def _update_company_last_updated(self, company_id: str, stamp: str, conn: Any | None = None) -> None:
        connection = conn or self._connect()
        connection.execute('UPDATE "Companies" SET "LastUpdated" = ? WHERE "CompanyID" = ?', (stamp, company_id))
        debug(f"Company '{company_id}' last updated timestamp set to '{stamp}'.")

    def append_response_history(self, company_id: str, question_id: str, old_value: str, new_value: str, email: str, stamp: str, conn: Any | None = None) -> None:
        connection = conn or self._connect()
        connection.execute('INSERT INTO "ResponseHistory" ("CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime") VALUES (?, ?, ?, ?, ?, ?)', (company_id, question_id, old_value, new_value, email, stamp))
        debug(f"Response history appended for question '{question_id}' for company '{company_id}'.")

    def submit(self, company_id: str, email: str) -> None:
        company = self.company(company_id)
        if company is None:
            raise ValueError("Company does not exist")
        stamp = now()
        conn = self._connect()
        conn.execute('UPDATE "Companies" SET "Status" = ?, "LastUpdated" = ?, "SubmittedBy" = ?, "SubmittedTime" = ? WHERE "CompanyID" = ?', ("Submitted", stamp, email, stamp, company_id))
        debug(f"Company '{company_id}' submitted successfully by '{email}' at '{stamp}'.")
        conn.commit()
        debug(f"Company '{company_id}' submitted successfully by '{email}' at '{stamp}'.")
        self.clear_cache()


class InMemoryRepository:
    """For local UI testing only. Each Streamlit session receives its own copy."""

    def __init__(self) -> None:
        self.tables = deepcopy(SEED)
        self._static_cache: dict[str, list[dict[str, str]]] = {}
        self._design_cache: dict[str, Any] | None = None
        self._login_cache: dict[str, list[dict[str, str]]] = {}
        self._history_counter = 0

    def ping(self) -> None:
        """No real I/O to check here: everything lives in-process memory. Present
        only for interface parity with the SQL-backed repositories so
        ensure_repository() can call it unconditionally."""
        return None

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

    def initialize_company_visibility(self, company_id: str) -> None:
        """Scoped, batched visibility initialization for a single company (in-memory).
        Mirrors the SQL implementations but operates on the in-memory tables.
        """
        company_id = str(company_id or "").strip()
        if not company_id:
            return
        questions = self.rows("Questions")
        if not questions:
            return

        try:
            from .condition_engine import is_question_visible
        except ImportError:
            from condition_engine import is_question_visible

        current_responses = dict(self.responses_for(company_id))
        conditions_by_question: dict[str, list[dict[str, str]]] = {}
        for row in self.rows("QuestionConditions"):
            conditions_by_question.setdefault(row["QuestionID"], []).append(row)

        self.tables.setdefault("QuestionVisibility", [])
        self.tables.setdefault("Responses", [])

        for question in questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
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

        seeded = getattr(self, "_visibility_initialized_companies", None)
        if seeded is None:
            seeded = set()
        seeded.add(company_id)
        self._visibility_initialized_companies = seeded
        self._design_cache = None

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

    def company_status(self, company_id: str) -> dict[str, str] | None:
        # Already in-process memory; no I/O to scope down, but kept for interface parity.
        return next((row for row in self.tables["Companies"] if row["CompanyID"] == company_id), None)

    def invalidate_static(self) -> None:
        for sheet_name in STATIC_TABLES:
            self._static_cache.pop(sheet_name, None)
        self._design_cache = None

    def invalidate_session(self) -> None:
        self._login_cache.clear()

    def invalidate_page_cache(self, company_id: str) -> None:
        return None

    def user(self, email: str, company_id: str) -> dict[str, str] | None:
        self.load_login_data()
        return next((row for row in self._login_cache["Users"] if row["Email"].lower() == email.lower() and row["CompanyID"] == company_id), None)

    def responses_for(self, company_id: str) -> dict[str, str]:
        return {row["QuestionID"]: row["ResponseValue"] for row in self.rows("Responses") if row["CompanyID"] == company_id}

    def visibility_for(self, company_id: str) -> dict[str, str]:
        return {row["QuestionID"]: row["Visible"] for row in self.tables.get("QuestionVisibility", []) if row["CompanyID"] == company_id}

    def save_responses(self, company_id: str, changes: dict[str, str], email: str) -> int:
        """Step 4.3, in-memory version: same collapse of "one refresh per changed
        answer" down to a single company-wide refresh after all changes are applied."""
        company_id = str(company_id or "").strip()
        if not company_id or not changes:
            return 0
        current_responses = self.responses_for(company_id)
        actual_changes = {
            str(question_id): str(value)
            for question_id, value in changes.items()
            if str(current_responses.get(str(question_id), "")) != str(value)
        }
        if not actual_changes:
            return 0
        for question_id, value in actual_changes.items():
            self.save_response(company_id, question_id, value, email)
        merged_responses = dict(current_responses)
        merged_responses.update(actual_changes)
        self.refresh_question_visibility(company_id, merged_responses)
        return len(actual_changes)

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

# Author: Tanakorn Tantanawat

"""Google Sheets persistence, with a developer-only in-memory fallback."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


SHEETS = ("Companies", "Users", "Questions", "QuestionOptions", "QuestionConditions", "Responses", "ResponseHistory")
STATIC_TABLES = {"Questions", "QuestionOptions", "QuestionConditions"}


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
    "Questions": [
        {"QuestionID": "Q001", "Sequence": "1", "QuestionText": "Describe your company", "AnswerType": "Text", "Required": "TRUE", "Active": "TRUE"},
        {"QuestionID": "Q002", "Sequence": "2", "QuestionText": "Lean implementation level", "AnswerType": "Choice", "Required": "TRUE", "Active": "TRUE"},
        {"QuestionID": "Q003", "Sequence": "3", "QuestionText": "Is TPM implemented?", "AnswerType": "Choice", "Required": "TRUE", "Active": "TRUE"},
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
            self._design_cache = {
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
            self._design_cache = {
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

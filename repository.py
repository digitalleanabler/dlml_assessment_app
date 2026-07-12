"""Google Sheets persistence, with a developer-only in-memory fallback."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


SHEETS = ("Companies", "Users", "Questions", "QuestionOptions", "QuestionConditions", "Responses", "ResponseHistory")


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

    def rows(self, worksheet: str) -> list[dict[str, str]]:
        return self.tables[worksheet]

    def company(self, company_id: str) -> dict[str, str] | None:
        return next((row for row in self.rows("Companies") if row["CompanyID"] == company_id), None)

    def user(self, email: str, company_id: str) -> dict[str, str] | None:
        return next((row for row in self.rows("Users") if row["Email"].lower() == email.lower() and row["CompanyID"] == company_id), None)

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
        self.rows("ResponseHistory").append({"HistoryID": f"H{len(self.rows('ResponseHistory')) + 1:04}", "CompanyID": company_id, "QuestionID": question_id, "OldValue": old, "NewValue": value, "ModifiedBy": email, "ModifiedTime": stamp})
        return True

    def submit(self, company_id: str, email: str) -> None:
        company = self.company(company_id)
        assert company
        stamp = now()
        company.update({"Status": "Submitted", "LastUpdated": stamp, "SubmittedBy": email, "SubmittedTime": stamp})


class GoogleSheetsRepository:
    """Repository for the production Google Spreadsheet contract."""

    def __init__(self, spreadsheet: Any) -> None:
        self.spreadsheet = spreadsheet

    @classmethod
    def from_service_account(cls, service_account_info: dict[str, Any], spreadsheet_id: str) -> "GoogleSheetsRepository":
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        credentials = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        return cls(gspread.authorize(credentials).open_by_key(spreadsheet_id))

    def rows(self, worksheet: str) -> list[dict[str, str]]:
        return self.spreadsheet.worksheet(worksheet).get_all_records(default_blank="")

    def _worksheet(self, name: str):
        return self.spreadsheet.worksheet(name)

    def _find_row(self, worksheet: str, predicate) -> tuple[int, dict[str, str]] | tuple[None, None]:
        for row_number, row in enumerate(self.rows(worksheet), start=2):
            if predicate(row):
                return row_number, row
        return None, None

    def company(self, company_id: str) -> dict[str, str] | None:
        return next((row for row in self.rows("Companies") if row["CompanyID"] == company_id), None)

    def user(self, email: str, company_id: str) -> dict[str, str] | None:
        return next((row for row in self.rows("Users") if row["Email"].lower() == email.lower() and row["CompanyID"] == company_id), None)

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
        history = self._worksheet("ResponseHistory")
        history.append_row([f"H{len(history.get_all_records(default_blank='')) + 1:04}", company_id, question_id, old, value, email, stamp], value_input_option="USER_ENTERED")
        return True

    def submit(self, company_id: str, email: str) -> None:
        row_number, current = self._find_row("Companies", lambda row: row["CompanyID"] == company_id)
        if not current:
            raise ValueError("Company does not exist")
        stamp = now()
        sheet = self._worksheet("Companies")
        headers = sheet.row_values(1)
        updated = {**current, "Status": "Submitted", "LastUpdated": stamp, "SubmittedBy": email, "SubmittedTime": stamp}
        sheet.update(f"A{row_number}", [[updated.get(header, "") for header in headers]])

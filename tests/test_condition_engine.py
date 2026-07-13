from app import authenticate, get_cached_responses
from repository import GoogleSheetsRepository, InMemoryRepository
from condition_engine import is_question_visible


Q3 = {"QuestionID": "Q003", "Active": "TRUE"}
RULE = [{"Seq": "1", "DependsOnQuestion": "Q002", "Operator": "=", "ExpectedValue": "ALL", "LogicalWithNext": "END"}]


def test_q3_hidden_before_answer_and_after_non_all_choice():
    assert not is_question_visible(Q3, RULE, {})
    assert not is_question_visible(Q3, RULE, {"Q002": "SOME"})


def test_q3_shown_for_all_choice():
    assert is_question_visible(Q3, RULE, {"Q002": "ALL"})


def test_active_question_can_be_hidden_by_configuration():
    assert not is_question_visible({"QuestionID": "Q003", "Active": "FALSE"}, RULE, {"Q002": "ALL"})


def test_static_tables_are_cached_per_repository_instance():
    repo = InMemoryRepository()
    first = repo.rows("Questions")
    second = repo.rows("Questions")
    assert first is not second
    assert first == second


def test_cached_responses_are_reused_until_explicit_reload():
    class DummyRepo:
        def __init__(self) -> None:
            self.calls = 0

        def responses_for(self, company_id: str) -> dict[str, str]:
            self.calls += 1
            return {"Q001": "seed"}

    repo = DummyRepo()
    session_state: dict[str, object] = {}

    first = get_cached_responses(repo, "C001", session_state)
    second = get_cached_responses(repo, "C001", session_state)

    assert first == {"Q001": "seed"}
    assert second == {"Q001": "seed"}
    assert repo.calls == 1


def test_sign_in_attempt_refreshes_login_data_once():
    class DummyRepo:
        def __init__(self) -> None:
            self.refresh_calls: list[bool] = []

        def load_login_data(self, force: bool = False) -> None:
            self.refresh_calls.append(force)

        def company(self, company_id: str) -> dict[str, str] | None:
            if company_id == "C001":
                return {"CompanyID": "C001", "CompanyName": "Northwind"}
            return None

        def user(self, email: str, company_id: str) -> dict[str, str] | None:
            if email == "maya@northwind.example" and company_id == "C001":
                return {"Email": email, "Name": "Maya", "CompanyID": company_id}
            return None

    repo = DummyRepo()

    company, user = authenticate(repo, "C001", "maya@northwind.example")

    assert company == {"CompanyID": "C001", "CompanyName": "Northwind"}
    assert user == {"Email": "maya@northwind.example", "Name": "Maya", "CompanyID": "C001"}
    assert repo.refresh_calls == [True]


def test_design_data_is_loaded_once_and_indexed_for_fast_lookup():
    class FakeWorksheet:
        def __init__(self, rows: list[dict[str, str]]) -> None:
            self.rows = rows
            self.calls = 0

        def get_all_records(self, default_blank: str = "") -> list[dict[str, str]]:
            self.calls += 1
            return [dict(row) for row in self.rows]

    class FakeSpreadsheet:
        def __init__(self) -> None:
            self.worksheets = {
                "Questions": FakeWorksheet([{"QuestionID": "Q001", "Sequence": "1"}]),
                "QuestionOptions": FakeWorksheet([{"QuestionID": "Q001", "OptionValue": "YES"}]),
                "QuestionConditions": FakeWorksheet([{"QuestionID": "Q001", "Seq": "1"}]),
            }

        def worksheet(self, name: str) -> FakeWorksheet:
            return self.worksheets[name]

    repo = GoogleSheetsRepository(FakeSpreadsheet())

    first = repo.design_data()
    second = repo.design_data()

    assert first is second
    assert first["Questions"] == [{"QuestionID": "Q001", "Sequence": "1"}]
    assert first["OptionsByQuestion"]["Q001"] == [{"QuestionID": "Q001", "OptionValue": "YES"}]
    assert first["ConditionsByQuestion"]["Q001"] == [{"QuestionID": "Q001", "Seq": "1"}]
    assert repo._rows_cache["Questions"] is not None


def test_rows_are_cached_for_non_design_worksheets():
    class FakeWorksheet:
        def __init__(self, rows: list[dict[str, str]]) -> None:
            self.rows = rows
            self.calls = 0

        def get_all_records(self, default_blank: str = "") -> list[dict[str, str]]:
            self.calls += 1
            return [dict(row) for row in self.rows]

    class FakeSpreadsheet:
        def __init__(self) -> None:
            self.worksheets = {
                "Companies": FakeWorksheet([{"CompanyID": "C001", "CompanyName": "Northwind"}]),
            }

        def worksheet(self, name: str) -> FakeWorksheet:
            return self.worksheets[name]

    repo = GoogleSheetsRepository(FakeSpreadsheet())

    first = repo.rows("Companies")
    second = repo.rows("Companies")

    assert first == second
    assert repo.spreadsheet.worksheet("Companies").calls == 1


def test_login_data_is_loaded_once_and_reused_for_company_and_user_lookup():
    class FakeWorksheet:
        def __init__(self, rows: list[dict[str, str]]) -> None:
            self.rows = rows
            self.calls = 0

        def get_all_records(self, default_blank: str = "") -> list[dict[str, str]]:
            self.calls += 1
            return [dict(row) for row in self.rows]

    class FakeSpreadsheet:
        def __init__(self) -> None:
            self.worksheets = {
                "Companies": FakeWorksheet([{"CompanyID": "C001", "CompanyName": "Northwind"}]),
                "Users": FakeWorksheet([{"Email": "maya@northwind.example", "Name": "Maya", "CompanyID": "C001"}]),
            }

        def worksheet(self, name: str) -> FakeWorksheet:
            return self.worksheets[name]

    repo = GoogleSheetsRepository(FakeSpreadsheet())

    repo.load_login_data()
    repo.company("C001")
    repo.user("maya@northwind.example", "C001")

    assert repo.spreadsheet.worksheet("Companies").calls == 1
    assert repo.spreadsheet.worksheet("Users").calls == 1


def test_login_data_can_be_refreshed_when_login_page_is_opened_again():
    class FakeWorksheet:
        def __init__(self, rows: list[dict[str, str]]) -> None:
            self.rows = rows
            self.calls = 0

        def get_all_records(self, default_blank: str = "") -> list[dict[str, str]]:
            self.calls += 1
            return [dict(row) for row in self.rows]

    class FakeSpreadsheet:
        def __init__(self) -> None:
            self.worksheets = {
                "Companies": FakeWorksheet([{"CompanyID": "C001", "CompanyName": "Northwind"}]),
                "Users": FakeWorksheet([{"Email": "maya@northwind.example", "Name": "Maya", "CompanyID": "C001"}]),
            }

        def worksheet(self, name: str) -> FakeWorksheet:
            return self.worksheets[name]

    repo = GoogleSheetsRepository(FakeSpreadsheet())

    repo.load_login_data()
    repo.load_login_data(force=True)

    assert repo.spreadsheet.worksheet("Companies").calls == 2
    assert repo.spreadsheet.worksheet("Users").calls == 2


def test_response_history_is_written_without_reading_existing_rows():
    class FakeWorksheet:
        def __init__(self, rows: list[dict[str, str]] | None = None) -> None:
            self.rows = rows or []
            self.appended_rows: list[list[str]] = []
            self.read_calls = 0

        def get_all_records(self, default_blank: str = "") -> list[dict[str, str]]:
            self.read_calls += 1
            raise AssertionError("ResponseHistory should not be read during write")

        def append_row(self, row: list[str], value_input_option: str = "USER_ENTERED") -> None:
            self.appended_rows.append(row)

    class FakeSpreadsheet:
        def __init__(self) -> None:
            self.worksheets = {
                "ResponseHistory": FakeWorksheet(),
            }

        def worksheet(self, name: str) -> FakeWorksheet:
            return self.worksheets[name]

    repo = GoogleSheetsRepository(FakeSpreadsheet())

    repo.append_response_history("C001", "Q001", "NO", "YES", "maya@northwind.example", "2024-01-01 00:00:00")

    assert repo.spreadsheet.worksheet("ResponseHistory").appended_rows[0][1:4] == ["C001", "Q001", "NO"]

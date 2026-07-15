from pathlib import Path

from openpyxl import Workbook

from app.repository import ExcelRepository


def test_excel_repository_round_trips_responses(tmp_path: Path) -> None:
    workbook_path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    workbook.remove(workbook.active)

    for sheet_name in ["Companies", "Users", "Pages", "Questions", "QuestionOptions", "QuestionConditions", "Responses", "ResponseHistory"]:
        workbook.create_sheet(title=sheet_name)

    workbook["Companies"].append(["CompanyID", "CompanyName", "Status", "LastUpdated", "SubmittedBy", "SubmittedTime"])
    workbook["Companies"].append(["C001", "Northwind", "Draft", "", "", ""])
    workbook["Users"].append(["Email", "Name", "CompanyID"])
    workbook["Users"].append(["maya@northwind.example", "Maya Chen", "C001"])
    workbook["Pages"].append(["PageID", "PageTitle"])
    workbook["Pages"].append(["P001", "Business Goal"])
    workbook["Questions"].append(["QuestionID", "PageID", "Sequence", "QuestionText", "AnswerType", "Required", "Active"])
    workbook["Questions"].append(["Q001", "P001", "1", "Describe your company", "Text", "TRUE", "TRUE"])
    workbook["QuestionOptions"].append(["OptionID", "QuestionID", "DisplayOrder", "OptionValue", "DisplayText"])
    workbook["QuestionConditions"].append(["LogicID", "QuestionID", "Seq", "LeftParen", "DependsOnQuestion", "Operator", "ExpectedValue", "RightParen", "LogicalWithNext"])
    workbook["Responses"].append(["CompanyID", "QuestionID", "ResponseValue", "LastModifiedBy", "LastModifiedTime"])
    workbook["ResponseHistory"].append(["HistoryID", "CompanyID", "QuestionID", "OldValue", "NewValue", "ModifiedBy", "ModifiedTime"])

    workbook.save(workbook_path)

    repo = ExcelRepository(workbook_path)

    assert repo.company("C001")["CompanyName"] == "Northwind"
    assert repo.user("maya@northwind.example", "C001")["Name"] == "Maya Chen"

    repo.save_response("C001", "Q001", "Hello", "maya@example.com")

    assert repo.responses_for("C001")["Q001"] == "Hello"
    assert repo.company("C001")["LastUpdated"]

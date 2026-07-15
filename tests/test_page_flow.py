from app import build_page_structure, get_page_readiness, reload_page_responses


def test_build_page_structure_groups_questions_by_page_and_includes_review():
    design_data = {
        "Pages": [
            {"PageID": "P001", "PageTitle": "Business Goal"},
            {"PageID": "P002", "PageTitle": "Business Objectives"},
        ],
        "Questions": [
            {"QuestionID": "Q001", "PageID": "P001", "Sequence": "1", "QuestionText": "Describe your company", "AnswerType": "Text", "Required": "TRUE", "Active": "TRUE"},
            {"QuestionID": "Q002", "PageID": "P002", "Sequence": "1", "QuestionText": "Lean implementation level", "AnswerType": "Choice", "Required": "TRUE", "Active": "TRUE"},
            {"QuestionID": "Q003", "PageID": "P002", "Sequence": "2", "QuestionText": "Is TPM implemented?", "AnswerType": "Choice", "Required": "FALSE", "Active": "TRUE"},
        ],
        "OptionsByQuestion": {},
        "ConditionsByQuestion": {},
    }

    pages = build_page_structure(design_data)

    assert [page["PageID"] for page in pages] == ["P001", "P002", "review"]
    assert [question["QuestionID"] for question in pages[1]["Questions"]] == ["Q002", "Q003"]
    assert pages[2]["PageTitle"] == "Review"


def test_page_readiness_counts_only_visible_required_questions():
    questions = [
        {"QuestionID": "Q001", "QuestionText": "First", "Required": "TRUE", "Active": "TRUE"},
        {"QuestionID": "Q002", "QuestionText": "Second", "Required": "TRUE", "Active": "TRUE"},
        {"QuestionID": "Q003", "QuestionText": "Third", "Required": "FALSE", "Active": "TRUE"},
    ]
    conditions_by_question = {
        "Q002": [{"Seq": "1", "DependsOnQuestion": "Q001", "Operator": "=", "ExpectedValue": "YES", "LogicalWithNext": "END"}],
    }

    readiness = get_page_readiness(questions, conditions_by_question, {"Q001": "YES", "Q002": ""})

    assert readiness["answered"] == 1
    assert readiness["total"] == 2
    assert readiness["completed"] is False


def test_reload_keeps_current_page_and_only_refreshes_saved_values():
    class DummyRepo:
        def responses_for(self, company_id: str):
            return {"Q001": "repo-value", "Q002": "repo-other"}

    draft_responses = {"Q001": "local-value", "Q002": "local-other"}
    state = {"saved_responses": {"Q001": "saved-value", "Q002": "saved-other"}, "active_page_id": "P002"}

    reloaded = reload_page_responses(DummyRepo(), "C001", draft_responses, state)

    assert reloaded["Q001"] == "repo-value"
    assert reloaded["Q002"] == "repo-other"
    assert state["active_page_id"] == "P002"

import streamlit as st

from app.app import (
    build_page_structure,
    get_page_readiness,
    reload_page_responses,
    reload_page_responses_for_page,
    sync_draft_responses,
    sync_question_visibility_state,
    sync_widget_state_from_responses,
)


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


def test_question_visibility_state_resets_reappearing_question_to_empty():
    pages = [
        {
            "PageID": "P001",
            "Questions": [
                {"QuestionID": "Q001", "QuestionText": "Trigger", "AnswerType": "Choice", "Required": "TRUE", "Active": "TRUE"},
                {"QuestionID": "Q002", "QuestionText": "Dependent", "AnswerType": "Text", "Required": "TRUE", "Active": "TRUE"},
            ],
        }
    ]
    design_data = {
        "ConditionsByQuestion": {
            "Q002": [{"Seq": "1", "DependsOnQuestion": "Q001", "Operator": "=", "ExpectedValue": "YES", "LogicalWithNext": "END"}],
        },
        "OptionsByQuestion": {},
    }
    draft_responses = {"Q001": "YES", "Q002": "previous answer"}
    session_state = {
        "visible_question_ids": set(),
        "previously_seen_question_ids": {"Q002"},
    }

    sync_question_visibility_state(pages, design_data, draft_responses, session_state)

    assert draft_responses["Q002"] == ""
    assert session_state["answer_Q002"] == ""


def test_question_visibility_state_preserves_new_reappearing_selection():
    pages = [
        {
            "PageID": "P001",
            "Questions": [
                {"QuestionID": "Q001", "QuestionText": "Trigger", "AnswerType": "Choice", "Required": "TRUE", "Active": "TRUE"},
                {"QuestionID": "Q002", "QuestionText": "Dependent", "AnswerType": "Choice", "Required": "TRUE", "Active": "TRUE"},
            ],
        }
    ]
    design_data = {
        "ConditionsByQuestion": {
            "Q002": [{"Seq": "1", "DependsOnQuestion": "Q001", "Operator": "=", "ExpectedValue": "YES", "LogicalWithNext": "END"}],
        },
        "OptionsByQuestion": {},
    }
    draft_responses = {"Q001": "YES", "Q002": ""}
    session_state = {
        "visible_question_ids": set(),
        "previously_seen_question_ids": {"Q002"},
        "answer_Q002": ("YES", "Yes"),
    }

    sync_question_visibility_state(pages, design_data, draft_responses, session_state)

    assert draft_responses["Q002"] == "YES"
    assert session_state["answer_Q002"] == ("YES", "Yes")


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

    draft_responses = {"Q001": "local-value", "Q002": "saved-other"}
    state = {"saved_responses": {"Q001": "saved-value", "Q002": "saved-other"}, "active_page_id": "P002"}

    reloaded = reload_page_responses(DummyRepo(), "C001", draft_responses, state)

    assert reloaded["Q001"] == "local-value"
    assert reloaded["Q002"] == "repo-other"
    assert state["active_page_id"] == "P002"


def test_explicit_reload_overrides_local_draft_with_latest_repository_values():
    class DummyRepo:
        def responses_for(self, company_id: str):
            return {"Q001": "repo-value"}

    draft_responses = {"Q001": "local-value"}
    state = {"saved_responses": {"Q001": "saved-value"}}

    reloaded = reload_page_responses(DummyRepo(), "C001", draft_responses, state, force=True)

    assert reloaded["Q001"] == "repo-value"


def test_sync_widget_state_updates_session_state_for_current_page():
    st.session_state.clear()
    st.session_state["answer_Q001"] = "old"
    st.session_state["answer_Q002"] = "old"

    responses = {"Q001": "new", "Q002": "newer"}
    sync_widget_state_from_responses(responses, force=True)

    assert st.session_state["answer_Q001"] == "new"
    assert st.session_state["answer_Q002"] == "newer"


def test_reload_page_responses_for_page_refreshes_only_destination_page_questions():
    class DummyRepo:
        def __init__(self):
            self.clear_calls = 0

        def clear_cache(self):
            self.clear_calls += 1

        def responses_for(self, company_id: str):
            return {"Q001": "repo-value", "Q002": "repo-other"}

    draft_responses = {"Q001": "local-value", "Q002": "saved-other"}
    state = {"protected_question_ids": {"Q001"}}
    repo = DummyRepo()

    reloaded = reload_page_responses_for_page(
        repo,
        "C001",
        draft_responses,
        state,
        [{"QuestionID": "Q001"}, {"QuestionID": "Q002"}],
    )

    assert reloaded["Q001"] == "local-value"
    assert reloaded["Q002"] == "saved-other"


def test_sync_widget_state_preserves_existing_user_input_by_default():
    st.session_state.clear()
    st.session_state["answer_Q001"] = "typed-value"

    responses = {"Q001": "repo-value"}
    sync_widget_state_from_responses(responses)

    assert st.session_state["answer_Q001"] == "typed-value"


def test_force_sync_widget_state_overrides_protected_user_input():
    st.session_state.clear()
    st.session_state["answer_Q001"] = "typed-value"

    responses = {"Q001": "repo-value"}
    sync_widget_state_from_responses(responses, force=True, protected_question_ids={"Q001"})

    assert st.session_state["answer_Q001"] == "repo-value"


def test_sync_draft_responses_preserves_current_user_input():
    class DummyRepo:
        def responses_for(self, company_id: str):
            return {"Q001": "repo-value", "Q002": "repo-other"}

    draft_responses = {"Q001": "typed-value"}
    sync_draft_responses(DummyRepo(), "C001", draft_responses)

    assert draft_responses["Q001"] == "typed-value"
    assert draft_responses["Q002"] == "repo-other"

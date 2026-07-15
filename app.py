from __future__ import annotations

import os
from typing import Any

import streamlit as st

from condition_engine import is_question_visible
from repository import GoogleSheetsRepository, InMemoryRepository


st.set_page_config(page_title="DLM Lifecycle Assessment", page_icon="📝", layout="centered")


@st.cache_resource
def get_repository() -> tuple[Any, bool]:
    print("Loading repository...")
    try:
        service_account_obj = st.secrets.get("gcp_service_account", {})
        service_account = dict(service_account_obj)
        print("service_account_keys", sorted(service_account.keys()))

        sheet_id = str(st.secrets.get("spreadsheet_id", "") or "")
        if not sheet_id:
            general_section = st.secrets.get("general", None)
            if general_section is not None and hasattr(general_section, "get"):
                sheet_id = str(general_section.get("spreadsheet_id", "") or "")

        sheet_id = sheet_id.strip()
        print("sheet_id_present", bool(sheet_id))
        print("service_account_present", bool(service_account))
        if service_account and sheet_id:
            print(f"Using Google Sheets repository with sheet_id={sheet_id}")
            return GoogleSheetsRepository.from_service_account(service_account, sheet_id), False

        print("Google Sheets secrets missing or incomplete; falling back to in-memory repository")
    except Exception as e:
        st.error(f"Secret loading failed: {e}")
        print(f"Secret loading failed: {e}")

    return InMemoryRepository(), True


def value_input(question: dict[str, str], options: list[dict[str, str]], current: str, disabled: bool) -> str:
    key = f"answer_{question['QuestionID']}"
    label = question["QuestionText"] + (" *" if str(question.get("Required", "")).upper() == "TRUE" else "")
    kind = question["AnswerType"].upper()
    if key not in st.session_state:
        st.session_state[key] = current
    if kind == "TEXT":
        return st.text_area(label, key=key, disabled=disabled)
    if kind == "NUMBER":
        return st.text_input(label, key=key, disabled=disabled, inputmode="numeric")
    if kind == "DATE":
        return st.text_input(label, key=key, disabled=disabled, placeholder="YYYY-MM-DD")
    if kind == "YESNO":
        options = [{"OptionValue": "", "DisplayText": "Select an answer"}, {"OptionValue": "YES", "DisplayText": "Yes"}, {"OptionValue": "NO", "DisplayText": "No"}]
    choices = [("", "Select an answer")] + [(option["OptionValue"], option["DisplayText"]) for option in options]
    if st.session_state[key] == current:
        st.session_state[key] = next((option for option in choices if option[0] == current), choices[0])
    choice = st.radio(label, choices, format_func=lambda item: item[1], key=key, disabled=disabled, horizontal=True)
    return choice[0]


def clear_answer_widgets() -> None:
    for key in list(st.session_state):
        if key.startswith("answer_"):
            del st.session_state[key]
    st.session_state.pop("responses_cache", None)


def authenticate(repo: Any, company_id: str, email: str) -> tuple[Any | None, Any | None]:
    if hasattr(repo, "load_login_data"):
        repo.load_login_data(force=True)
    company = repo.company(company_id) if hasattr(repo, "company") else None
    user = repo.user(email, company["CompanyID"]) if company else None
    return company, user


def get_cached_responses(repo: Any, company_id: str, session_state: dict[str, Any]) -> dict[str, str]:
    cache_key = "responses_cache"
    if cache_key not in session_state:
        session_state[cache_key] = dict(repo.responses_for(company_id))
    return dict(session_state[cache_key])


def get_missing_required_questions(
    questions: list[dict[str, str]],
    conditions_by_question: dict[str, list[dict[str, Any]]],
    responses: dict[str, str],
) -> list[str]:
    missing: list[str] = []
    for question in questions:
        question_id = question["QuestionID"]
        if str(question.get("Required", "")).upper() != "TRUE":
            continue
        if str(question.get("Active", "")).upper() != "TRUE":
            continue
        if not is_question_visible(question, conditions_by_question.get(question_id, []), responses):
            continue
        value = responses.get(question_id, "")
        if not str(value).strip():
            missing.append(question["QuestionText"])
    return missing


def main() -> None:
    st.session_state.setdefault("identity", None)
    repo, demo_mode = get_repository()
    if demo_mode:
        st.warning("Developer demo mode: using in-memory seed data. Configure Google Sheets secrets for shared persistence.")

    if st.session_state.identity is None:
        st.title("DLM Lifecycle Assessment")
        st.caption("Sign in to work on your company’s shared assessment.")
        with st.form("sign_in"):
            company_id = st.text_input("Company ID")
            email = st.text_input("Email address", placeholder="name@gmail.com")
            submitted = st.form_submit_button("Continue", type="primary")
        if submitted:
            cleaned_company_id = company_id.strip()
            cleaned_email = email.strip().lower()
            if not cleaned_company_id or not cleaned_email:
                st.warning("Enter both your Company ID and Email address.")
            else:
                company, user = authenticate(repo, cleaned_company_id, cleaned_email)
                if not user:
                    st.warning("We couldn't verify your Company ID and Email Address. Please check your details and try again. If the problem persists, contact your Project Administrator.")
                else:
                    clear_answer_widgets()
                    st.session_state.identity = {**user, "CompanyName": company["CompanyName"]}
                    st.rerun()
        st.info("Use the Company ID provided by your Project Administrator and the Email Address you registered with the project. If you do not have a Company ID, please contact your Project Administrator before proceeding.")
        return

    identity = st.session_state.identity
    company = repo.company(identity["CompanyID"])
    assert company
    readonly = company["Status"] == "Submitted"
    st.title("DLM Lifecycle Assessment")
    st.caption(f"{identity['CompanyName']} · Signed in as {identity['Name']} ({identity['Email']})")
    if st.button("Reload shared survey"):
        repo.clear_cache()
        st.session_state["responses_cache"] = dict(repo.responses_for(identity["CompanyID"]))
        st.rerun()
    if readonly:
        st.success(f"Submitted by {company['SubmittedBy']} on {company['SubmittedTime']}. This survey is read-only.")

    design_data = repo.design_data()
    questions = sorted(design_data["Questions"], key=lambda row: int(row["Sequence"]))
    responses = get_cached_responses(repo, identity["CompanyID"], st.session_state)

    draft_responses = dict(responses)
    visible_questions: list[dict[str, str]] = []
    for question in questions:
        question_id = question["QuestionID"]
        conditions = design_data["ConditionsByQuestion"].get(question_id, [])
        if not is_question_visible(question, conditions, draft_responses):
            continue
        visible_questions.append(question)
        options = sorted(
            design_data["OptionsByQuestion"].get(question_id, []),
            key=lambda row: int(row.get("DisplayOrder", 0) or 0),
        )
        st.divider()
        draft_responses[question_id] = value_input(question, options, responses.get(question_id, ""), readonly)

    st.session_state["responses_cache"] = dict(draft_responses)

    if not readonly:
        st.divider()
        st.markdown(
            """
            <style>
            [data-testid="stHorizontalBlock"]:has(#assessment-actions) button {
                min-width: 10rem;
            }
            [data-testid="stHorizontalBlock"]:has(#assessment-actions) button {
                background-color: #ffffff !important;
                border-color: #c62828 !important;
                color: #c62828 !important;
            }
            [data-testid="stHorizontalBlock"]:has(#assessment-actions) button:hover {
                background-color: #c62828 !important;
                border-color: #c62828 !important;
                color: #ffffff !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        left_actions, right_actions = st.columns(2)
        with left_actions:
            save_clicked = st.button("Save responses", type="primary")
            st.markdown('<span id="assessment-actions"></span>', unsafe_allow_html=True)
        with right_actions:
            _, submit_action = st.columns(2)
            with submit_action:
                submit_clicked = st.button("Submit assessment", type="primary", use_container_width=True)

        if save_clicked:
            saved_count = sum(
                repo.save_response(identity["CompanyID"], question["QuestionID"], draft_responses.get(question["QuestionID"], ""), identity["Email"])
                for question in visible_questions
            )
            if saved_count:
                st.session_state["responses_cache"] = dict(draft_responses)
                st.success(f"Saved {saved_count} response{'s' if saved_count != 1 else ''}. You can continue editing.")
            else:
                st.info("There are no changed responses to save.")
        if submit_clicked:
            missing = get_missing_required_questions(
                questions,
                design_data["ConditionsByQuestion"],
                draft_responses,
            )
            if missing:
                st.error("Complete all required questions before submitting: " + ", ".join(missing))
            else:
                for question in visible_questions:
                    repo.save_response(identity["CompanyID"], question["QuestionID"], draft_responses.get(question["QuestionID"], ""), identity["Email"])
                st.session_state["responses_cache"] = dict(draft_responses)
                repo.submit(identity["CompanyID"], identity["Email"])
                st.rerun()

    with st.sidebar:
        st.subheader("Collaboration")
        st.write("All authorised users in this company edit the same response set.")
        st.caption(
            "Save your progress at any time.<br>"
            "Responses can be edited until submission.<br>"
            "If multiple users edit the same response, the latest saved change will be kept.<br>"
            "Once submitted, no further changes can be made.",
            unsafe_allow_html=True,
        )
        if st.button("Sign out"):
            clear_answer_widgets()
            st.session_state.identity = None
            st.rerun()


if __name__ == "__main__":
    main()

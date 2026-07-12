from __future__ import annotations

import os
from typing import Any

import streamlit as st

from condition_engine import is_question_visible
from repository import GoogleSheetsRepository, InMemoryRepository


st.set_page_config(page_title="DLML Collaborative Assessment", page_icon="📝", layout="centered")


@st.cache_resource
def get_repository() -> tuple[Any, bool]:
    """Use the live Sheet when production secrets exist; otherwise allow local testing."""
    try:
        service_account = dict(st.secrets["gcp_service_account"])
        sheet_id = str(st.secrets.get("spreadsheet_id", os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")))
        if service_account and sheet_id:
            return GoogleSheetsRepository.from_service_account(service_account, sheet_id), False
    except (KeyError, FileNotFoundError):
        pass
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


def main() -> None:
    st.session_state.setdefault("identity", None)
    repo, demo_mode = get_repository()
    if demo_mode:
        st.warning("Developer demo mode: using in-memory seed data. Configure Google Sheets secrets for shared persistence.")

    if st.session_state.identity is None:
        st.title("DLML Collaborative Assessment")
        st.caption("Sign in to work on your company’s shared assessment.")
        with st.form("sign_in"):
            company_name = st.text_input("Company name")
            email = st.text_input("Google email address", placeholder="name@gmail.com")
            submitted = st.form_submit_button("Continue", type="primary")
        if submitted:
            cleaned_company_name = company_name.strip()
            cleaned_email = email.strip().lower()
            company = next(
                (row for row in repo.rows("Companies") if row["CompanyName"].strip().casefold() == cleaned_company_name.casefold()),
                None,
            )
            user = repo.user(cleaned_email, company["CompanyID"]) if company else None
            if not cleaned_company_name or not cleaned_email:
                st.warning("Enter both your company name and Google email address.")
            elif not user:
                st.warning("The company name and Google email address do not match an authorised user record.")
            else:
                clear_answer_widgets()
                st.session_state.identity = {**user, "CompanyName": company["CompanyName"]}
                st.rerun()
        st.info("The entered company name and Google email are checked against the Companies and Users worksheets before access is granted.")
        return

    identity = st.session_state.identity
    company = repo.company(identity["CompanyID"])
    assert company
    readonly = company["Status"] == "Submitted"
    st.title("DLML Collaborative Assessment")
    st.caption(f"{identity['CompanyName']} · Signed in as {identity['Name']} ({identity['Email']})")
    if st.button("Reload shared survey"):
        st.rerun()
    if readonly:
        st.success(f"Submitted by {company['SubmittedBy']} on {company['SubmittedTime']}. This survey is read-only.")

    questions = sorted(repo.rows("Questions"), key=lambda row: int(row["Sequence"]))
    all_conditions = repo.rows("QuestionConditions")
    all_options = repo.rows("QuestionOptions")
    responses = repo.responses_for(identity["CompanyID"])

    draft_responses = dict(responses)
    visible_questions: list[dict[str, str]] = []
    for question in questions:
        conditions = [row for row in all_conditions if row["QuestionID"] == question["QuestionID"]]
        if not is_question_visible(question, conditions, draft_responses):
            continue
        visible_questions.append(question)
        options = sorted([row for row in all_options if row["QuestionID"] == question["QuestionID"]], key=lambda row: int(row.get("DisplayOrder", 0) or 0))
        draft_responses[question["QuestionID"]] = value_input(question, options, responses.get(question["QuestionID"], ""), readonly)

    if not readonly:
        st.divider()
        st.markdown(
            """
            <style>
            [data-testid="stHorizontalBlock"]:has(#assessment-actions) button {
                min-width: 10rem;
            }
            [data-testid="stHorizontalBlock"]:has(#assessment-actions) [data-testid="stColumn"]:first-child button {
                background-color: #c62828;
                border-color: #c62828;
                color: #ffffff;
            }
            [data-testid="stHorizontalBlock"]:has(#assessment-actions) [data-testid="stColumn"]:first-child button:hover {
                background-color: #9f1f1f;
                border-color: #9f1f1f;
                color: #ffffff;
            }
            [data-testid="stHorizontalBlock"]:has(#assessment-actions) [data-testid="stColumn"]:last-child button {
                background-color: #ffffff;
                border-color: #c62828;
                color: #c62828;
                float: right;
            }
            [data-testid="stHorizontalBlock"]:has(#assessment-actions) [data-testid="stColumn"]:last-child button:hover {
                background-color: #fff5f5;
                border-color: #9f1f1f;
                color: #9f1f1f;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        left_actions, right_actions = st.columns(2)
        with left_actions:
            save_clicked = st.button("Save responses", type="secondary")
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
                st.success(f"Saved {saved_count} response{'s' if saved_count != 1 else ''}. You can continue editing.")
            else:
                st.info("There are no changed responses to save.")
        if submit_clicked:
            missing = [question["QuestionText"] for question in visible_questions if str(question.get("Required", "")).upper() == "TRUE" and not draft_responses.get(question["QuestionID"])]
            if missing:
                st.error("Complete all visible required questions before submitting: " + ", ".join(missing))
            else:
                for question in visible_questions:
                    repo.save_response(identity["CompanyID"], question["QuestionID"], draft_responses.get(question["QuestionID"], ""), identity["Email"])
                repo.submit(identity["CompanyID"], identity["Email"])
                st.rerun()

    with st.sidebar:
        st.subheader("Collaboration")
        st.write("All authorised users in this company edit the same response set.")
        st.caption("Each changed value creates a ResponseHistory record. Hidden responses are retained in the backend.")
        if st.button("Sign out"):
            clear_answer_widgets()
            st.session_state.identity = None
            st.rerun()


if __name__ == "__main__":
    main()

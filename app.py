from __future__ import annotations

import os
from typing import Any

import streamlit as st

try:
    from .condition_engine import is_question_visible
    from .repository import InMemoryRepository, SQLiteRepository, TursoRepository, LIBSQL_IMPORT_ERROR
except ImportError:  # pragma: no cover - support running app.py directly
    from condition_engine import is_question_visible
    from repository import InMemoryRepository, SQLiteRepository, TursoRepository, LIBSQL_IMPORT_ERROR


st.set_page_config(page_title="DLM Lifecycle Assessment", page_icon="📝", layout="centered")


@st.cache_resource
def get_repository() -> tuple[Any, bool]:
    print("Loading repository...")

    # Load environment variable to determine which repository to use
    app_env = (os.getenv("app_env") or "").strip().lower()
    st.info(f"app_env: {app_env}")
    print(f"app_env: {app_env}")

    # If app_env is "local"
    if app_env == "local":
        try:
            print("Loading local SQLite repository")
            return SQLiteRepository(), False
        except Exception as exc:
            st.warning(f"Unable to load local SQLite repository: {exc}. Using temporary in-memory data instead.")
            print(f"Unable to load local SQLite repository: {exc}. Using temporary in-memory data instead")
            return InMemoryRepository(), True
    else:
        st.warning(f"Unrecognized app_env '{app_env}'. Using temporary in-memory data instead.")
        print(f"Unrecognized app_env '{app_env}'. Using temporary in-memory data instead.")
        
        is_cloud = os.getenv("STREAMLIT_RUNTIME") is not None
        if is_cloud:
            st.info("Running in Streamlit Cloud environment.")
            print("Running in Streamlit Cloud environment.")
        else:
            st.info("Running in local environment.")
            print("Running in local environment.")


        # If app_env is not 'local' (e.g. 'local1', or '' (cloud deployment)), attempt to load Turso repository
        try:
            print("Loading Turso repository")
            turso_section = st.secrets.get("turso", {})
            turso_config = dict(turso_section)
            database_url = str(turso_config.get("TURSO_DATABASE_URL", "") or "").strip()
            auth_token = str(turso_config.get("TURSO_AUTH_TOKEN", "") or "").strip()

            print("turso_database_url_present", bool(database_url))
            print("turso_auth_token_present", bool(auth_token))
            if database_url:
                try:
                    from .repository import libsql_available
                except ImportError:  # pragma: no cover - support running app.py directly
                    from repository import libsql_available
                
                if not libsql_available():
                    st.error(f"Turso support is unavailable because the 'libsql' package is not installed: {LIBSQL_IMPORT_ERROR}. Using temporary in-memory data instead.")
                    print(f"Turso support is unavailable because the 'libsql' package is not installed: {LIBSQL_IMPORT_ERROR}. Using temporary in-memory data instead.")
                    return InMemoryRepository(), True
                
                try:
                    return TursoRepository(database_url=database_url, auth_token=auth_token), False
                except Exception as exc:
                    st.error(f"Unable to connect to Turso: {exc}. Using temporary in-memory data instead.")
                    print(f"Unable to connect to Turso: {exc}. Using temporary in-memory data instead.")
                    return InMemoryRepository(), True

            st.error("Turso credentials are missing or incomplete. Using temporary in-memory data instead.")
            print("Turso credentials are missing or incomplete. Using temporary in-memory data instead.")
            return InMemoryRepository(), True
        
        except Exception as e:
            st.error(f"Turso repository initialization failed: {e}. Using temporary in-memory data instead.")
            print(f"Repository initialization failed: {e}. Using temporary in-memory data instead.")
            return InMemoryRepository(), True
    
    # If app_env is not set or unrecognized, default to in-memory repository
    #st.warning("Environment variable 'app_env' is not set or unrecognized. Using temporary in-memory data instead.")
    #print("Environment variable 'app_env' is not set or unrecognized. Using temporary in-memory data instead.")
    #return InMemoryRepository(), True


def value_input(question: dict[str, str], options: list[dict[str, str]], current: str, disabled: bool, draft_responses: dict[str, str]) -> str:
    question_id = str(question["QuestionID"])
    key = f"answer_{question_id}"
    label = question["QuestionText"] + (" *" if str(question.get("Required", "")).upper() == "TRUE" else "")
    kind = question["AnswerType"].upper()

    if key not in st.session_state:
        st.session_state[key] = current

    if kind == "TEXT":
        value = st.text_area(label, key=key, disabled=disabled)
    elif kind == "NUMBER":
        value = st.text_input(label, key=key, disabled=disabled, inputmode="numeric")
    elif kind == "DATE":
        value = st.text_input(label, key=key, disabled=disabled, placeholder="YYYY-MM-DD")
    else:
        if kind == "YESNO":
            options = [{"OptionValue": "", "DisplayText": "Select an answer"}, {"OptionValue": "YES", "DisplayText": "Yes"}, {"OptionValue": "NO", "DisplayText": "No"}]
        choices = [("", "Select an answer")] + [(option["OptionValue"], option["DisplayText"]) for option in options]
        if key not in st.session_state or st.session_state[key] == current:
            st.session_state[key] = next((option for option in choices if option[0] == current), choices[0])
        choice = st.radio(label, choices, format_func=lambda item: item[1], key=key, disabled=disabled, horizontal=True)
        value = choice[0]

    if kind in {"TEXT", "NUMBER", "DATE"}:
        widget_state = st.session_state.get(key, value)
        if isinstance(widget_state, tuple):
            value = widget_state[0]
        else:
            value = widget_state

    if str(value) != str(current):
        protected_question_ids = st.session_state.setdefault("protected_question_ids", set())
        protected_question_ids.add(question_id)

    draft_responses[question_id] = value
    return value


def clear_answer_widgets() -> None:
    for key in list(st.session_state):
        if key.startswith("answer_"):
            del st.session_state[key]
    st.session_state.pop("responses_cache", None)
    st.session_state.pop("page_draft_responses", None)
    st.session_state.pop("saved_responses", None)
    st.session_state.pop("active_page_id", None)
    st.session_state.pop("sidebar_page_selection", None)
    st.session_state.pop("protected_question_ids", None)
    st.session_state.pop("visible_question_ids", None)
    st.session_state.pop("previously_seen_question_ids", None)


def sync_question_visibility_state(
    pages: list[dict[str, Any]],
    design_data: dict[str, Any],
    draft_responses: dict[str, str],
    session_state: dict[str, Any],
) -> None:
    previous_visible_question_ids = set(session_state.get("visible_question_ids", set()))
    previously_seen_question_ids = set(session_state.get("previously_seen_question_ids", set()))
    current_visible_question_ids: set[str] = set()
    protected_question_ids = set(session_state.setdefault("protected_question_ids", set()))

    for page in pages:
        for question in page.get("Questions", []):
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue

            conditions = design_data["ConditionsByQuestion"].get(question_id, [])
            is_visible = is_question_visible(question, conditions, draft_responses)
            if is_visible:
                current_visible_question_ids.add(question_id)
                was_previously_seen = question_id in previously_seen_question_ids
                previously_seen_question_ids.add(question_id)
                if was_previously_seen and question_id not in previous_visible_question_ids:
                    answer_key = f"answer_{question_id}"
                    current_answer = session_state.get(answer_key)
                    if current_answer is not None:
                        normalized_answer = current_answer[0] if isinstance(current_answer, tuple) else current_answer
                        if str(normalized_answer).strip():
                            draft_responses[question_id] = normalized_answer
                            session_state[answer_key] = current_answer
                        else:
                            draft_responses[question_id] = ""
                            session_state[answer_key] = ""
                    else:
                        draft_responses[question_id] = ""
                        session_state[answer_key] = ""
                    protected_question_ids.add(question_id)
            else:
                if question_id in previous_visible_question_ids:
                    draft_responses.pop(question_id, None)
                    session_state.pop(f"answer_{question_id}", None)
                    protected_question_ids.discard(question_id)

    session_state["visible_question_ids"] = current_visible_question_ids
    session_state["previously_seen_question_ids"] = previously_seen_question_ids
    session_state["protected_question_ids"] = protected_question_ids


def sync_widget_state_from_responses(
    responses: dict[str, str],
    *,
    force: bool = False,
    protected_question_ids: set[str] | None = None,
) -> None:
    protected = set(protected_question_ids or set())
    for question_id, value in responses.items():
        key = f"answer_{question_id}"
        if force:
            st.session_state[key] = value
            continue
        if question_id in protected:
            continue
        if key not in st.session_state:
            st.session_state[key] = value


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


def build_page_structure(design_data: dict[str, Any]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    page_rows = design_data.get("Pages", [])
    if page_rows:
        for row in page_rows:
            page_id = str(row.get("PageID", "") or "").strip()
            if not page_id:
                continue
            pages.append({"PageID": page_id, "PageTitle": str(row.get("PageTitle", page_id) or page_id), "Questions": []})
    else:
        pages.append({"PageID": "default", "PageTitle": "Assessment", "Questions": []})

    page_lookup = {page["PageID"]: page for page in pages}
    questions = sorted(design_data.get("Questions", []), key=lambda row: int(str(row.get("Sequence", "0") or "0")))
    for question in questions:
        page_id = str(question.get("PageID", "") or "").strip()
        if page_id and page_id in page_lookup:
            page_lookup[page_id]["Questions"].append(question)
    for page in pages:
        page["Questions"].sort(key=lambda row: int(str(row.get("Sequence", "0") or "0")))

    pages.append({"PageID": "review", "PageTitle": "Review", "Questions": []})
    return pages


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


def get_page_readiness(
    questions: list[dict[str, str]],
    conditions_by_question: dict[str, list[dict[str, Any]]],
    responses: dict[str, str],
) -> dict[str, Any]:
    answered = 0
    total = 0
    missing: list[str] = []
    for question in questions:
        question_id = question["QuestionID"]
        if str(question.get("Required", "")).upper() != "TRUE":
            continue
        if str(question.get("Active", "")).upper() != "TRUE":
            continue
        if not is_question_visible(question, conditions_by_question.get(question_id, []), responses):
            continue
        total += 1
        value = str(responses.get(question_id, "") or "").strip()
        if value:
            answered += 1
        else:
            missing.append(question["QuestionText"])
    return {"answered": answered, "total": total, "completed": answered == total, "missing": missing}


def merge_responses_with_repository(current: dict[str, str], refreshed: dict[str, str], saved: dict[str, str], protected_question_ids: set[str] | None = None) -> dict[str, str]:
    merged = dict(current)
    protected = protected_question_ids or set()
    for question_id, value in refreshed.items():
        current_value = merged.get(question_id, "")
        saved_value = saved.get(question_id, "")
        if question_id in protected:
            continue
        if str(current_value).strip() and str(current_value) != str(saved_value):
            continue
        merged[question_id] = value
    return merged


def sync_draft_responses(repo: Any, company_id: str, draft_responses: dict[str, str]) -> dict[str, str]:
    if hasattr(repo, "clear_cache"):
        repo.clear_cache()
    refreshed = dict(repo.responses_for(company_id))
    saved = dict(st.session_state.get("saved_responses", {}))
    protected_question_ids = set(st.session_state.get("protected_question_ids", set()))
    merged = merge_responses_with_repository(draft_responses, refreshed, saved, protected_question_ids)
    draft_responses.clear()
    draft_responses.update(merged)
    st.session_state["responses_cache"] = dict(refreshed)
    st.session_state["page_draft_responses"] = dict(draft_responses)
    st.session_state["saved_responses"] = dict(saved or refreshed)
    sync_widget_state_from_responses(merged, force=True, protected_question_ids=protected_question_ids)
    return draft_responses


def reload_page_responses(repo: Any, company_id: str, draft_responses: dict[str, str], session_state: dict[str, Any], *, force: bool = False) -> dict[str, str]:
    if hasattr(repo, "clear_cache"):
        repo.clear_cache()
    refreshed = dict(repo.responses_for(company_id))
    saved = dict(session_state.get("saved_responses", {}))
    protected_question_ids = set(session_state.get("protected_question_ids", set()))
    if force:
        draft_responses.clear()
        draft_responses.update(refreshed)
        merged = dict(refreshed)
    else:
        merged = merge_responses_with_repository(draft_responses, refreshed, saved, protected_question_ids)
        draft_responses.clear()
        draft_responses.update(merged)
    session_state["responses_cache"] = dict(refreshed)
    session_state["page_draft_responses"] = dict(draft_responses)
    session_state["saved_responses"] = dict(saved or refreshed)
    sync_widget_state_from_responses(merged, force=True, protected_question_ids=protected_question_ids)
    return draft_responses


def reload_page_responses_for_page(
    repo: Any,
    company_id: str,
    draft_responses: dict[str, str],
    session_state: dict[str, Any],
    questions: list[dict[str, str]],
) -> dict[str, str]:
    if hasattr(repo, "clear_cache"):
        repo.clear_cache()
    refreshed = dict(repo.responses_for(company_id))
    saved = dict(session_state.get("saved_responses", {}))
    protected_question_ids = set(session_state.get("protected_question_ids", set()))
    page_question_ids = {str(question["QuestionID"]) for question in questions if question.get("QuestionID")}

    for question_id, value in list(refreshed.items()):
        if question_id not in page_question_ids:
            continue
        current_value = draft_responses.get(question_id, "")
        saved_value = saved.get(question_id, "")
        if question_id in protected_question_ids:
            continue
        if str(current_value).strip() and str(current_value) != str(saved_value):
            continue
        draft_responses[question_id] = value

    session_state["responses_cache"] = dict(refreshed)
    session_state["page_draft_responses"] = dict(draft_responses)
    session_state["saved_responses"] = dict(saved or refreshed)
    sync_widget_state_from_responses(draft_responses, force=True, protected_question_ids=protected_question_ids)
    return draft_responses


def save_page_questions(repo: Any, company_id: str, email: str, questions: list[dict[str, str]], draft_responses: dict[str, str]) -> int:
    existing = dict(st.session_state.get("saved_responses", {}))
    saved_count = 0
    for question in questions:
        question_id = question["QuestionID"]
        key = f"answer_{question_id}"
        widget_value = st.session_state.get(key, None)
        if isinstance(widget_value, tuple):
            value = widget_value[0]
        elif widget_value is None:
            value = draft_responses.get(question_id, "")
        else:
            value = widget_value
        draft_responses[question_id] = value
        if str(existing.get(question_id, "")) != str(value):
            repo.save_response(company_id, question_id, value, email)
            saved_count += 1
    st.session_state["responses_cache"] = dict(draft_responses)
    st.session_state["page_draft_responses"] = dict(draft_responses)
    st.session_state["saved_responses"] = dict(draft_responses)
    protected_question_ids = st.session_state.setdefault("protected_question_ids", set())
    for question in questions:
        protected_question_ids.discard(question["QuestionID"])
    return saved_count


def render_question_page(page: dict[str, Any], design_data: dict[str, Any], draft_responses: dict[str, str], readonly: bool) -> None:
    for question in page.get("Questions", []):
        question_id = question["QuestionID"]
        conditions = design_data["ConditionsByQuestion"].get(question_id, [])
        if not is_question_visible(question, conditions, draft_responses):
            continue
        options = sorted(
            design_data["OptionsByQuestion"].get(question_id, []),
            key=lambda row: int(row.get("DisplayOrder", 0) or 0),
        )
        st.divider()
        value_input(question, options, draft_responses.get(question_id, ""), readonly, draft_responses)


def main() -> None:
    st.session_state.setdefault("identity", None)

    ####################################################################################################
    # If not logged in, create login page

    if st.session_state.identity is None:
        st.title("DLM Lifecycle Assessment")
        st.caption("Sign in to work on your company’s shared assessment.")
        with st.form("sign_in"):
            company_id = st.text_input("Company ID")
            email = st.text_input("Email address", placeholder="name@gmail.com")
            submitted = st.form_submit_button("Enter", type="primary")
        
        # If login form is submitted, attempt to authenticate
        if submitted:
            cleaned_company_id = company_id.strip()
            cleaned_email = email.strip().lower()
            if not cleaned_company_id or not cleaned_email:
                st.warning("Enter both your Company ID and Email address.")
            else:
                repo, demo_mode = get_repository()   # NEW
                company, user = authenticate(repo, cleaned_company_id, cleaned_email)
                if not user:
                    st.warning("We couldn't verify your Company ID and Email Address. Please check your details and try again. If the problem persists, contact your Project Administrator.")
                else:
                    clear_answer_widgets()
                    st.session_state.identity = {**user, "CompanyName": company["CompanyName"]}
                    st.rerun()

        st.info("Use the Company ID provided by your Project Administrator and the Email Address you registered with the project. If you do not have a Company ID, please contact your Project Administrator before proceeding.")
        return
    
    ####################################################################################################
    # If already logged in
    
    # Prepare data
    identity = st.session_state.identity
    repo, demo_mode = get_repository()   # NEW
    company = repo.company(identity["CompanyID"])
    assert company

    # Set readonly tag if company already submitted the form
    readonly = company["Status"] == "Submitted"

    #--------------------------------------------------
    # Create main page header
    st.title("DLM Lifecycle Assessment")
    st.caption(f"{identity['CompanyName']} · Signed in as {identity['Name']} ({identity['Email']})")
    if st.button("Reload shared survey"):
        repo.clear_cache() # if click, cache cleared and where do we reload the repo??? !!!
        current_page_id = st.session_state.get("active_page_id", None)
        page_drafts = st.session_state.setdefault("page_draft_responses", {})
        reload_page_responses(repo, identity["CompanyID"], page_drafts, st.session_state, force=True)
        if current_page_id is not None:
            st.session_state["active_page_id"] = current_page_id
            st.session_state["sidebar_page_selection"] = current_page_id
        st.rerun()
    if readonly:
        st.success(f"Submitted by {company['SubmittedBy']} on {company['SubmittedTime']}. This survey is read-only.")

    design_data = repo.design_data()
    pages = build_page_structure(design_data)
    all_questions = [question for page in pages[:-1] for question in page["Questions"]]
    
    # Create draft responses
    draft_responses = st.session_state.setdefault("page_draft_responses", {})

    # If empty, sync it to the repository responses
    if not draft_responses:
        sync_draft_responses(repo, identity["CompanyID"], draft_responses)
    # I not empty, sync it to widget state
    else:
        sync_widget_state_from_responses(draft_responses)

    sync_question_visibility_state(pages, design_data, draft_responses, st.session_state)

   #--------------------------------------------------
   # Create sidebar
    with st.sidebar:
        st.subheader("Pages")
        page_options = [page["PageID"] for page in pages]
        page_titles = {page["PageID"]: page["PageTitle"] for page in pages}
        st.session_state.setdefault("sidebar_page_selection", page_options[0])
        selected_page_id = st.radio("Navigate", page_options, format_func=lambda page_id: page_titles[page_id], key="sidebar_page_selection")
        if "active_page_id" not in st.session_state:
            st.session_state["active_page_id"] = selected_page_id
        previous_page_id = st.session_state["active_page_id"]
        if previous_page_id != selected_page_id:
            if previous_page_id != "review":
                saved_count = save_page_questions(repo, identity["CompanyID"], identity["Email"], [page for page in pages if page["PageID"] == previous_page_id][0]["Questions"], draft_responses)
                if saved_count:
                    st.toast(f"Saved {saved_count} response{'s' if saved_count != 1 else ''} on {page_titles[previous_page_id]}.")
            selected_page_questions = [page for page in pages if page["PageID"] == selected_page_id][0]["Questions"]
            reload_page_responses_for_page(repo, identity["CompanyID"], draft_responses, st.session_state, selected_page_questions)
        st.session_state["active_page_id"] = selected_page_id

        st.divider()
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

    selected_page = next(page for page in pages if page["PageID"] == selected_page_id)

    #--------------------------------------------------
    # Create main page content
    if selected_page_id != "review":

        st.subheader(selected_page["PageTitle"])
        render_question_page(selected_page, design_data, draft_responses, readonly)
        if not readonly:
            st.divider()
            save_clicked = st.button("Save this page", type="primary")
            if save_clicked:
                saved_count = save_page_questions(repo, identity["CompanyID"], identity["Email"], selected_page["Questions"], draft_responses)
                if saved_count:
                    st.success(f"Saved {saved_count} response{'s' if saved_count != 1 else ''} on {selected_page['PageTitle']}.")
                else:
                    st.info("There are no changed responses to save on this page.")
        return

    #--------------------------------------------------
    # Create review page
    if selected_page_id == "review":

        st.subheader("Review")
        st.caption("Check the readiness of your submission before sending it.")
        for page in pages[:-1]:
            readiness = get_page_readiness(page["Questions"], design_data["ConditionsByQuestion"], draft_responses)
            st.write(f"{page['PageTitle']}: {readiness['answered']}/{readiness['total']} required visible questions answered")
            if readiness["total"]:
                st.progress(readiness["answered"] / readiness["total"])
            else:
                st.progress(0)
        missing = get_missing_required_questions(all_questions, design_data["ConditionsByQuestion"], draft_responses)
        if missing:
            st.warning("Missing required visible answers: " + ", ".join(missing))
        else:
            st.success("All required visible questions are answered.")
        if not readonly:
            if st.button("Submit assessment", type="primary", use_container_width=True):
                missing = get_missing_required_questions(all_questions, design_data["ConditionsByQuestion"], draft_responses)
                if missing:
                    st.error("Complete all required questions before submitting: " + ", ".join(missing))
                else:
                    save_page_questions(repo, identity["CompanyID"], identity["Email"], all_questions, draft_responses)
                    repo.submit(identity["CompanyID"], identity["Email"])
                    st.rerun()
        return

if __name__ == "__main__":
    main()

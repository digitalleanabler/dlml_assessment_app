from __future__ import annotations
import os
import sys
import inspect
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import streamlit as st
try:
    from .condition_engine import is_question_visible
    from .repository import InMemoryRepository, SQLiteRepository, TursoRepository, LIBSQL_IMPORT_ERROR
except ImportError:  # pragma: no cover - support running app.py directly
    from condition_engine import is_question_visible
    from repository import InMemoryRepository, SQLiteRepository, TursoRepository, LIBSQL_IMPORT_ERROR


st.set_page_config(page_title="DLM Lifecycle Assessment", page_icon="📝", layout="centered")


def debug(msg):
    frame = inspect.currentframe().f_back
    filename = Path(frame.f_code.co_filename).name
    print(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}: {filename}: {frame.f_lineno}: {frame.f_code.co_name}: {msg}")
    sys.stdout.flush()
    

# Function to initialize host-level visibility for all companies
def initialize_host_visibility(repo: Any) -> bool:
    # Tracked as an attribute on the repo instance itself, not a module-level
    # global. A fresh instance (e.g. after a connection-recovery repo swap via
    # select_repository()) will not have this attribute set, so it correctly
    # re-runs bootstrap on the replacement repo instead of silently skipping it
    # because some *earlier* instance had already run once.
    if getattr(repo, "_host_visibility_initialized", False):
        return False

    # Step 4.7: prefer the batched bulk bootstrap - a small, constant number of
    # statements regardless of companies x questions - when the repository supports
    # it. Fall back to the old per-company loop only for repositories with no
    # network/disk round-trip cost to optimize away in the first place
    # (InMemoryRepository).
    if hasattr(repo, "initialize_all_visibility"):
        try:
            repo.initialize_all_visibility()
            repo._host_visibility_initialized = True
            return True
        except Exception as exc:
            print(f"Bulk host-level visibility initialization failed: {exc}")
            return False

    if not hasattr(repo, "refresh_question_visibility"):
        repo._host_visibility_initialized = True
        return False

    try:
        companies = repo.rows("Companies") if hasattr(repo, "rows") else []
        for company in companies:
            company_id = str(company.get("CompanyID", "") or "").strip()
            if not company_id:
                continue
            responses = repo.responses_for(company_id) if hasattr(repo, "responses_for") else {}
            repo.refresh_question_visibility(company_id, responses)

        repo._host_visibility_initialized = True
        return True
    except Exception as exc:
        print(f"Host-level visibility initialization failed: {exc}")
        return False


def select_repository(app_env: str | None = None, secrets: Any | None = None) -> tuple[Any, bool]:
    general_section = secrets.get("general", {}) if secrets is not None else {}
    resolved_env = (app_env or os.getenv("app_env") or str(general_section.get("app_env", "")) or "").strip().lower()
    debug(f"resolved_env={resolved_env}")

    if resolved_env == "local":
        try:
            print("Loading local SQLite repository")
            return SQLiteRepository(), False
        except Exception as exc:
            st.warning(f"Unable to load local SQLite repository: {exc}. Using temporary in-memory data instead.")
            print(f"Unable to load local SQLite repository: {exc}. Using temporary in-memory data instead")
            return InMemoryRepository(), True

    turso_section = secrets.get("turso", {}) if secrets is not None else {}
    turso_config = dict(turso_section)
    database_url = str(turso_config.get("TURSO_DATABASE_URL", "") or "").strip()
    auth_token = str(turso_config.get("TURSO_AUTH_TOKEN", "") or "").strip()

    if database_url and auth_token:
        try:
            print("Loading Turso repository")
            from .repository import libsql_available
        except ImportError:  # pragma: no cover - support running app.py directly
            from repository import libsql_available

        if not libsql_available():
            st.error(f"Turso support is unavailable because the 'libsql' package is not installed: {LIBSQL_IMPORT_ERROR}. Using temporary in-memory data instead.")
            print(f"Turso support is unavailable because the 'libsql' package is not installed: {LIBSQL_IMPORT_ERROR}. Using temporary in-memory data instead")
            if resolved_env in {"turso", "cloud"}:
                return InMemoryRepository(), True
        else:
            try:
                repo = TursoRepository(database_url=database_url, auth_token=auth_token)
                repo.rows("Companies")
                return repo, False
            except Exception as exc:
                st.error(f"Unable to connect to Turso: {exc}. Using temporary in-memory data instead.")
                print(f"Unable to connect to Turso: {exc}. Using temporary in-memory data instead")
                if resolved_env in {"turso", "cloud"}:
                    return InMemoryRepository(), True

    if resolved_env in {"turso", "cloud"}:
        st.info("Cloud mode is enabled, but Turso credentials were not available or usable. Using temporary in-memory data instead for this session.")
        return InMemoryRepository(), True

    try:
        print("Loading local SQLite repository")
        return SQLiteRepository(), False
    except Exception as exc:
        st.warning(f"Unable to load local SQLite repository: {exc}. Using temporary in-memory data instead.")
        print(f"Unable to load local SQLite repository: {exc}. Using temporary in-memory data instead")
        return InMemoryRepository(), True


@st.cache_resource
def get_repository() -> tuple[Any, bool]:
    print("Loading repository...")
    try:
        repo, demo_mode = select_repository(secrets=st.secrets)
        initialize_host_visibility(repo)
        return repo, demo_mode
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        st.error(f"Repository initialization failed: {exc}. Using temporary in-memory data instead.")
        print(f"Repository initialization failed: {exc}. Using temporary in-memory data instead.")
        return InMemoryRepository(), True


def ensure_repository(repo: Any, secrets: Any | None = None) -> tuple[Any, bool]:
    try:
        # Fix 4: design_data() is cached after its first successful call, so on
        # every later rerun this used to be a no-op that verified nothing. ping()
        # always performs a real, cheap round trip (SELECT 1) so connectivity is
        # actually re-checked every rerun, not just the first one this process.
        if hasattr(repo, "ping"):
            repo.ping()
        else:
            repo.design_data()
        return repo, False
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        if hasattr(repo, "clear_cache"):
            try:
                repo.clear_cache()
            except Exception:
                pass
        if hasattr(repo, "_disconnect"):
            try:
                repo._disconnect()
            except Exception:
                pass
        st.error(f"Cloud repository access failed: {exc}. Switching to fallback repository.")
        print(f"Cloud repository access failed: {exc}. Switching to fallback repository.")
        if hasattr(get_repository, "clear"):
            try:
                get_repository.clear()
            except Exception:
                pass
        new_repo, demo_mode = select_repository("", secrets)
        initialize_host_visibility(new_repo)
        return new_repo, demo_mode


def _is_valid_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _is_valid_date(text: str) -> bool:
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def value_input(question: dict[str, str], options: list[dict[str, str]], current: str, disabled: bool, draft_responses: dict[str, str]) -> str:
    question_id = str(question["QuestionID"])
    key = f"answer_{question_id}"
    label = question["QuestionText"] + (" *" if str(question.get("Required", "")).upper() == "TRUE" else "")
    label = f"[{question_id}]: {label}"
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

    # Fix 6: NUMBER/DATE previously had no format validation at all - any string
    # would be silently stored and saved. This is advisory only (it does not block
    # entry or saving) so it doesn't change save/visibility behavior, but it gives
    # the person immediate feedback on a likely-malformed answer instead of the bad
    # value going unnoticed until someone reads it downstream.
    stripped_value = str(value).strip()
    if not disabled and stripped_value:
        if kind == "NUMBER" and not _is_valid_number(stripped_value):
            st.caption(":red[This doesn't look like a valid number.]")
        elif kind == "DATE" and not _is_valid_date(stripped_value):
            st.caption(":red[Enter the date as YYYY-MM-DD.]")

    draft_responses[question_id] = value
    return value


def clear_answer_widgets() -> None:
    for key in list(st.session_state):
        if key.startswith("answer_"):
            del st.session_state[key]
    st.session_state.pop("page_session", None)
    st.session_state.pop("active_page_id", None)
    st.session_state.pop("sidebar_page_selection", None)


@dataclass
class PageSession:
    """Step 4.6: single source of truth for page-level response/visibility state,
    replacing the previously-fragmented page_draft_responses / responses_cache /
    saved_responses / protected_question_ids / visible_question_ids /
    previously_seen_question_ids session_state keys and the five functions
    (merge_responses_with_repository, sync_widget_state_from_responses,
    reload_page_responses, reload_page_responses_for_page,
    reload_responses_for_navigation, sync_question_visibility_state) that used to
    coordinate them independently.

    - `responses` is the authoritative in-memory answer set (source of truth for the
      whole session - covers Requirement 3/4's "retain answer when hidden, restore
      when visible again" for free, since a hidden question's value simply stays in
      this dict untouched until it's rendered again).
    - `saved_snapshot` is the last value we know to be persisted in the DB (from a
      scoped load or a save), used only to figure out which questions are "dirty"
      (edited locally but not yet written back).
    - `visibility` is recomputed every rerun as a pure function of `responses` (4.2)
      and is never read back from the DB.
    """

    responses: dict[str, str] = field(default_factory=dict)
    saved_snapshot: dict[str, str] = field(default_factory=dict)
    visibility: dict[str, bool] = field(default_factory=dict)
    loaded: bool = False

    def dirty_ids(self) -> set[str]:
        """Questions whose in-memory value differs from the last known persisted
        value - i.e. unsaved local edits that must never be clobbered by a refresh."""
        return {
            question_id
            for question_id, value in self.responses.items()
            if str(value) != str(self.saved_snapshot.get(question_id, ""))
        }

    def capture_widgets(self, questions: list[dict[str, str]], session_state: dict[str, Any]) -> None:
        """Pull whatever Streamlit already has in session_state for these questions'
        widgets into `responses`. Must run before any DB read this rerun, so an
        in-progress edit is captured before we ask "is this dirty?"."""
        for question in questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            key = f"answer_{question_id}"
            if key in session_state:
                value = session_state[key]
                if isinstance(value, tuple):
                    value = value[0]
                self.responses[question_id] = value

    def refresh_from_repository(self, repo: Any, company_id: str, session_state: dict[str, Any], *, force: bool = False) -> dict[str, str]:
        """Load-on-entry (Requirement 3/4): one scoped read (4.5) of this company's
        responses. A question that's currently dirty (unsaved local edit) keeps its
        local value unless `force` is set (initial load / explicit reload button); a
        concurrent edit from another user is still picked up into `saved_snapshot` so
        dirtiness stays correct against the newest baseline either way. Any value that
        *is* refreshed here is written straight into the widget's session_state key
        too - a lingering widget key from a previous visit to this page would
        otherwise shadow it, since Streamlit only seeds a widget's default when its
        key is absent."""
        refreshed = dict(repo.responses_for(company_id))
        dirty = set() if force else self.dirty_ids()
        for question_id, value in refreshed.items():
            if force or question_id not in dirty:
                self.responses[question_id] = value
                session_state[f"answer_{question_id}"] = value
        self.saved_snapshot = refreshed
        self.loaded = True
        return refreshed

    def recompute_visibility(self, all_questions: list[dict[str, str]], conditions_by_question: dict[str, list[dict[str, Any]]]) -> dict[str, bool]:
        """Preserve-on-hide (UX spec): visibility is a pure function of the in-memory
        `responses` dict - never read back from the DB (4.2). QuestionVisibility in
        the database stays a write-only persisted snapshot for external
        consumers/audit."""
        visibility: dict[str, bool] = {}
        for question in all_questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            conditions = conditions_by_question.get(question_id, [])
            visibility[question_id] = is_question_visible(question, conditions, self.responses)
        self.visibility = visibility
        return visibility

    def clear_hidden_widgets(self, all_questions: list[dict[str, str]], session_state: dict[str, Any]) -> None:
        """Drop the widget key for any currently-hidden question, so if it becomes
        visible again it re-seeds from `responses` (the authoritative value) rather
        than showing whatever stale value Streamlit last held under that key."""
        for question in all_questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if question_id and not self.visibility.get(question_id, True):
                session_state.pop(f"answer_{question_id}", None)

    def save_to_repository(self, repo: Any, company_id: str, email: str, questions: list[dict[str, str]], session_state: dict[str, Any]) -> int:
        """Save-on-exit: capture the latest widget edits, diff against the last known
        persisted snapshot restricted to this set of questions, and write the whole
        batch in one call (4.3/4.4)."""
        self.capture_widgets(questions, session_state)
        page_question_ids = {str(question["QuestionID"]) for question in questions if question.get("QuestionID")}
        changes = {question_id: self.responses[question_id] for question_id in self.dirty_ids() & page_question_ids}
        if not changes:
            return 0
        saved_count = repo.save_responses(company_id, changes, email)
        for question_id, value in changes.items():
            self.saved_snapshot[question_id] = value
        return saved_count


def get_page_session(session_state: dict[str, Any]) -> PageSession:
    if "page_session" not in session_state:
        session_state["page_session"] = PageSession()
    return session_state["page_session"]


def authenticate(repo: Any, company_id: str, email: str) -> tuple[Any | None, Any | None]:
    try:
        if hasattr(repo, "load_login_data"):
            repo.load_login_data(force=True)
        company = repo.company(company_id) if hasattr(repo, "company") else None

        # One-time, per-process company-scoped visibility initialization.
        if company and hasattr(repo, "initialize_company_visibility"):
            seeded = getattr(repo, "_visibility_initialized_companies", None)
            if seeded is None:
                seeded = set()
                setattr(repo, "_visibility_initialized_companies", seeded)
            cid = str(company.get("CompanyID", "") or "").strip()
            if cid and cid not in seeded:
                try:
                    repo.initialize_company_visibility(cid)
                except Exception as exc:
                    # Log but don't fail authentication for a visibility seed error.
                    print(f"initialize_company_visibility failed for '{cid}': {exc}")
                else:
                    seeded.add(cid)

        user = repo.user(email, company["CompanyID"]) if company else None
        return company, user
    except Exception as exc:
        print(f"Repository authentication failed: {exc}")
        raise


def refresh_company_state(repo: Any, company_id: str) -> Any | None:
    # Every rerun (any widget interaction) used to call clear_cache(), wiping the
    # app-level (Pages/Questions/...) and session-level (Companies/Users) tiers even
    # though only "has this company's Status changed" needs checking here. That forced
    # a full static-table + full-table Companies/Users reload on every click.
    # We only actually need Status/SubmittedBy/SubmittedTime, so use the narrow,
    # WHERE-scoped read instead and leave the static/session caches alone.
    if hasattr(repo, "company_status"):
        try:
            status_row = repo.company_status(company_id)
            if status_row is not None:
                return status_row
        except Exception:
            pass

    # Fallback for any repository that doesn't implement the scoped read.
    if hasattr(repo, "clear_cache"):
        try:
            repo.clear_cache()
        except Exception:
            pass
    if hasattr(repo, "load_login_data"):
        try:
            repo.load_login_data(force=True)
        except Exception:
            pass
    return repo.company(company_id) if hasattr(repo, "company") else None


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
    visibility: dict[str, bool],
    responses: dict[str, str],
) -> list[str]:
    missing: list[str] = []
    for question in questions:
        question_id = str(question["QuestionID"])
        if str(question.get("Required", "")).upper() != "TRUE":
            continue
        if not visibility.get(question_id, True):
            continue
        value = responses.get(question_id, "")
        if not str(value).strip():
            missing.append(question["QuestionText"])
    return missing


def get_page_readiness(
    questions: list[dict[str, str]],
    visibility: dict[str, bool],
    responses: dict[str, str],
) -> dict[str, Any]:
    answered = 0
    total = 0
    missing: list[str] = []
    for question in questions:
        question_id = str(question["QuestionID"])
        if str(question.get("Required", "")).upper() != "TRUE":
            continue
        if not visibility.get(question_id, True):
            continue
        total += 1
        value = str(responses.get(question_id, "") or "").strip()
        if value:
            answered += 1
        else:
            missing.append(question["QuestionText"])
    return {"answered": answered, "total": total, "completed": answered == total, "missing": missing}


def render_question_page(
    page: dict[str, Any],
    design_data: dict[str, Any],
    page_session: PageSession,
    readonly: bool,
) -> None:
    for question in page.get("Questions", []):
        question_id = str(question["QuestionID"])
        if not page_session.visibility.get(question_id, True):
            continue
        options = sorted(
            design_data["OptionsByQuestion"].get(question_id, []),
            key=lambda row: int(row.get("DisplayOrder", 0) or 0),
        )
        st.divider()
        value_input(question, options, page_session.responses.get(question_id, ""), readonly, page_session.responses)


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
                repo, demo_mode = get_repository()
                repo, demo_mode = ensure_repository(repo, st.secrets)
                try:
                    company, user = authenticate(repo, cleaned_company_id, cleaned_email)
                except BaseException as exc:  # pragma: no cover - handle runtime panics from libsql
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    get_repository.clear()
                    repo, demo_mode = select_repository("", st.secrets)
                    initialize_host_visibility(repo)
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
    repo, demo_mode = get_repository()
    repo, demo_mode = ensure_repository(repo, st.secrets)
    try:
        company = refresh_company_state(repo, identity["CompanyID"])
    except BaseException:
        get_repository.clear()
        repo, demo_mode = select_repository("", st.secrets)
        initialize_host_visibility(repo)
        company = refresh_company_state(repo, identity["CompanyID"])
    assert company

    if identity.get("CompanyStatus") != company.get("Status"):
        identity["CompanyStatus"] = company.get("Status")

    # Set readonly tag if company already submitted the form
    readonly = str(company.get("Status", "")).strip().upper() == "SUBMITTED"

    #--------------------------------------------------
    # Create main page header
    st.title("DLM Lifecycle Assessment")
    st.caption(f"{identity['CompanyName']} · Signed in as {identity['Name']} ({identity['Email']})")

    design_data = repo.design_data()
    pages = build_page_structure(design_data)
    all_questions = [question for page in pages[:-1] for question in page["Questions"]]

    # Step 4.6: one PageSession is the single source of truth for responses/
    # visibility/dirtiness this session - replaces page_draft_responses,
    # responses_cache, saved_responses, protected_question_ids, visible_question_ids,
    # and previously_seen_question_ids.
    page_session = get_page_session(st.session_state)
    is_first_load = not page_session.loaded

    if st.button("Reload shared survey"):
        current_page_id = st.session_state.get("active_page_id", None)
        page_session.refresh_from_repository(repo, identity["CompanyID"], st.session_state, force=True)
        if current_page_id is not None:
            st.session_state["active_page_id"] = current_page_id
            st.session_state["sidebar_page_selection"] = current_page_id
        st.rerun()

    if readonly:
        st.success(f"Submitted by {company['SubmittedBy']} on {company['SubmittedTime']}. This survey is read-only.")

    # Load-on-entry (first visit this session), otherwise just capture whatever the
    # user's already typed into visible widgets before we recompute visibility.
    if is_first_load:
        page_session.refresh_from_repository(repo, identity["CompanyID"], st.session_state, force=True)
    else:
        page_session.capture_widgets(all_questions, st.session_state)

    # Preserve-on-hide: pure in-memory recompute (4.2), then drop any now-hidden
    # widget's key so it re-seeds from `responses` if it becomes visible again.
    page_session.recompute_visibility(all_questions, design_data["ConditionsByQuestion"])
    page_session.clear_hidden_widgets(all_questions, st.session_state)

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
                previous_page_questions = [page for page in pages if page["PageID"] == previous_page_id][0]["Questions"]
                saved_count = page_session.save_to_repository(repo, identity["CompanyID"], identity["Email"], previous_page_questions, st.session_state)
                if saved_count:
                    st.toast(f"Saved {saved_count} response{'s' if saved_count != 1 else ''} on {page_titles[previous_page_id]}.")
            # Save-on-exit above; load-on-entry for the page being navigated to.
            # The review page always force-reloads (it summarizes every page), a
            # regular page does a merge-reload that never clobbers unsaved local edits.
            page_session.refresh_from_repository(repo, identity["CompanyID"], st.session_state, force=(selected_page_id == "review"))
            page_session.recompute_visibility(all_questions, design_data["ConditionsByQuestion"])
            page_session.clear_hidden_widgets(all_questions, st.session_state)
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

        st.divider()
        st.subheader("Admin")

        # Fix 5: invalidate_static()/invalidate_session() (step 4.1) were previously
        # never called by app.py, meaning a project administrator editing
        # Pages/Questions/QuestionOptions/QuestionConditions or Companies/Users
        # directly in the database would not see those changes reflected in a
        # running session until the process itself restarted. This gives an
        # explicit, opt-in way to pick up those changes without a restart, while
        # leaving the page-level (Responses/QuestionVisibility) cache untouched -
        # in-progress answers on this page are unaffected.
        with st.expander("Reload design & users"):
            st.caption("Use this only if the survey design (pages, questions, options) or company/user list changed after this session started.")
            if st.button("Reload now", key="admin_reload_design_users"):
                # Note: this button is rendered after st.radio(key="sidebar_page_selection")
                # above has already been instantiated this run, so its session_state
                # key cannot be reassigned here (Streamlit raises a
                # StreamlitAPIException). That's fine - reloading design/user data
                # doesn't change which page is selected, so session_state already
                # holds the right value and needs no adjustment before rerunning.
                if hasattr(repo, "invalidate_static"):
                    repo.invalidate_static()
                if hasattr(repo, "invalidate_session"):
                    repo.invalidate_session()
                st.rerun()

    selected_page = next(page for page in pages if page["PageID"] == selected_page_id)

    #--------------------------------------------------
    # Create main page content
    if selected_page_id != "review":

        st.subheader(selected_page["PageTitle"])
        render_question_page(selected_page, design_data, page_session, readonly)
        if not readonly:
            st.divider()
            save_clicked = st.button("Save this page", type="primary")
            if save_clicked:
                saved_count = page_session.save_to_repository(repo, identity["CompanyID"], identity["Email"], selected_page["Questions"], st.session_state)
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
            readiness = get_page_readiness(page["Questions"], page_session.visibility, page_session.responses)
            left_col, right_col = st.columns([3, 2], vertical_alignment="center")
            with left_col:
                status_text = f"{readiness['answered']}/{readiness['total']} required visible questions answered"
                st.markdown(
                    f"""
                    <div style="margin: 0 0 0.15rem 0; line-height: 1.2;">
                        <div><strong>{page['PageTitle']}</strong></div>
                        <div style="font-size: 0.875em; color: rgba(49, 51, 63, 0.6); margin-top: 0.1rem;">{status_text}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with right_col:
                if readiness["total"]:
                    progress_value = readiness["answered"] / readiness["total"]
                    if readiness["answered"] == readiness["total"]:
                        st.progress(progress_value, "completed")
                    else:
                        st.progress(progress_value, "incomplete")
                else:
                    st.progress(1.0, "completed")
        missing = get_missing_required_questions(all_questions, page_session.visibility, page_session.responses)
        if missing:
            st.warning("Missing required visible answers: " + ", ".join(missing))
        else:
            st.success("All required visible questions are answered.")
        if not readonly:
            if st.button("Submit assessment", type="primary", use_container_width=True):
                missing = get_missing_required_questions(all_questions, page_session.visibility, page_session.responses)
                if missing:
                    st.error("Complete all required questions before submitting: " + ", ".join(missing))
                else:
                    page_session.save_to_repository(repo, identity["CompanyID"], identity["Email"], all_questions, st.session_state)
                    repo.submit(identity["CompanyID"], identity["Email"])
                    st.rerun()
        return

if __name__ == "__main__":
    main()

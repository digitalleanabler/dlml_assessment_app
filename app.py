from __future__ import annotations
import os
import re
import sys
import inspect
import base64
import io
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape as xml_escape
import streamlit as st
from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph
try:
    from .condition_engine import is_question_visible
    from .repository import InMemoryRepository, SQLiteRepository, TursoRepository, LIBSQL_IMPORT_ERROR
    from .config import RESOURCE_URL
except ImportError:  # pragma: no cover - support running app.py directly
    from condition_engine import is_question_visible
    from repository import InMemoryRepository, SQLiteRepository, TursoRepository, LIBSQL_IMPORT_ERROR
    from config import RESOURCE_URL


st.set_page_config(page_title="DLM Lifecycle Assessment", page_icon="📝", layout="centered")


def debug(msg):
    # frame = inspect.currentframe().f_back
    # filename = Path(frame.f_code.co_filename).name
    # print(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}: {filename}: {frame.f_lineno}: {frame.f_code.co_name}: {msg}")
    # sys.stdout.flush()
    pass


def default_privacy_notice_path() -> Path:
    return Path(__file__).resolve().parent / "privacy_notice.md"


def load_privacy_notice() -> tuple[str, str]:
    """Reads privacy_notice.md (sibling of app.py) and parses its
    '**Notice Version:** vX.Y' metadata line - the sole source of truth for the
    notice text and its version (03-business-rules.md, "Privacy notice consent";
    06-conventions.md, "Privacy notice version"). Read fresh every call rather than
    cached, so a version bump on disk is always picked up the next time the gate
    runs (i.e. on the next login - see "Not re-checked mid-session" below).
    Returns (version_string, full_notice_text); version_string is "" if the
    metadata line isn't found or the file is missing."""
    path = default_privacy_notice_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Unable to read privacy notice at {path}: {exc}")
        return "", ""
    match = re.search(r"\*\*Notice Version:\*\*\s*(\S+)", text)
    version = match.group(1).strip() if match else ""
    return version, text


def notice_major_version(version: str) -> int | None:
    """Parse the integer major component before the first '.' in a vMAJOR.MINOR
    privacy-notice version string, e.g. 'v1.2' -> 1 (05-glossary.md, 'Major version
    (notice)'). Only this component is ever compared when deciding whether a user
    must re-accept. Returns None if the string doesn't parse."""
    cleaned = str(version or "").strip()
    if cleaned[:1].lower() == "v":
        cleaned = cleaned[1:]
    major_part = cleaned.split(".", 1)[0]
    try:
        return int(major_part)
    except ValueError:
        return None
    

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


def question_display_text(question: dict[str, str]) -> str:
    full_text = str(question.get("FullAssessmentQuestion", "") or "").strip()
    return full_text if full_text else question["QuestionText"]


def value_input(question: dict[str, str], options: list[dict[str, str]], current: str, disabled: bool, draft_responses: dict[str, str]) -> str:
    question_id = str(question["QuestionID"])
    key = f"answer_{question_id}"
    label = question_display_text(question) + (" *" if str(question.get("Required", "")).upper() == "TRUE" else "")
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
            options = [{"OptionValue": "YES", "DisplayText": "Yes"}, {"OptionValue": "NO", "DisplayText": "No"}]
        valid_options = [
            (option["OptionValue"], option["DisplayText"])
            for option in options
            if str(option.get("OptionValue", "")).strip()
        ]
        if valid_options:
            choices = [("", "Select an answer")] + valid_options
            if key not in st.session_state or st.session_state[key] == current:
                st.session_state[key] = next((option for option in choices if option[0] == current), choices[0])
            choice = st.radio(label, choices, format_func=lambda item: item[1], key=key, disabled=disabled, horizontal=True)
            value = choice[0]
        else:
            st.markdown(label)
            value = ""

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
    # Consent gate (ADR-008): "Not re-checked mid-session" means once-per-login, not
    # once-per-process - so it must be cleared on every identity change (a fresh
    # sign-in or a sign-out/decline), the same lifecycle as the rest of this
    # session's page-level state.
    st.session_state.pop("consent_confirmed", None)


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
    # ADR-011: page-scoped, visit-local baseline used only to decide whether
    # leaving the currently open page should show the unsaved-changes warning.
    # Distinct from `saved_snapshot`/`dirty_ids()` above, which compare against
    # the database globally and exist only to protect an in-progress edit on a
    # *different* page from a merge-reload triggered elsewhere (ADR-001). See
    # 05-glossary.md, "Entry snapshot" vs "Dirty" vs "Page-dirty".
    entry_responses: dict[str, str] = field(default_factory=dict)
    entry_visibility: dict[str, bool] = field(default_factory=dict)
    entry_page_id: str | None = None

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

    def refresh_from_repository(self, repo: Any, company_id: str, session_state: dict[str, Any], *, force: bool = False, page_questions: list[dict[str, str]] | None = None) -> dict[str, str]:
        """Load-on-entry (Requirement 3/4): one scoped read (4.5) of this company's
        responses. A question that's currently dirty (unsaved local edit) keeps its
        local value unless `force` is set (initial load / explicit reload button); a
        concurrent edit from another user is still picked up into `saved_snapshot` so
        dirtiness stays correct against the newest baseline either way.

        `self.responses` (the authoritative, whole-survey dict driving
        `recompute_visibility()`) is always refreshed company-wide, regardless of
        `page_questions`. Only the *widget* session_state key - `answer_{id}` - is
        additionally pre-seeded, and only for `page_questions` (the page about to be
        rendered this same rerun, if any): a lingering widget key from a previous
        visit to *that* page would otherwise shadow the refreshed value, since
        Streamlit only seeds a widget's default when its key is absent. Writing that
        key for a question on some *other* page is not just unnecessary - Streamlit
        drops a widget's session_state key once that widget goes a rerun without
        being instantiated, and a bare-string value written here for a Choice/YesNo
        question does not survive being carried across that drop the way a real
        widget interaction does. A subsequent `capture_widgets()` call can then read
        back a corrupted/blank value for a question that was never actually
        touched - see 08-status-and-open-questions.md, [g13] - so this must stay
        scoped to the page actually being (re)rendered, never `all_questions`."""
        refreshed = dict(repo.responses_for(company_id))
        dirty = set() if force else self.dirty_ids()
        scoped_ids = {str(question["QuestionID"]) for question in (page_questions or []) if question.get("QuestionID")}
        for question_id, value in refreshed.items():
            if force or question_id not in dirty:
                self.responses[question_id] = value
                if question_id in scoped_ids:
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

    def capture_entry_snapshot(self, page_id: str, page_questions: list[dict[str, str]]) -> None:
        """ADR-011: record this one page's responses/visibility at the moment it
        was loaded - first load, an ordinary merge-reload arrival, a force-reload
        (Review, "Reload shared survey") - and again immediately after a
        successful "Save this page"/"Submit assessment". This is the baseline
        `is_page_dirty()` compares against. Deliberately *not* captured when the
        user chooses "Return to the current page" from the unsaved-changes
        warning - that path is a cancelled navigation, not a reload (see
        03-business-rules.md)."""
        page_question_ids = {str(question["QuestionID"]) for question in page_questions if question.get("QuestionID")}
        self.entry_page_id = page_id
        self.entry_responses = {question_id: str(self.responses.get(question_id, "")) for question_id in page_question_ids}
        self.entry_visibility = {question_id: bool(self.visibility.get(question_id, True)) for question_id in page_question_ids}

    def is_page_dirty(self, page_questions: list[dict[str, str]]) -> bool:
        """ADR-011, "Page-dirty": True if any question on this page currently
        differs - in value **or** in visibility - from this page's own entry
        snapshot. A visibility flip counts on its own, even if the underlying
        stored value hasn't changed (e.g. a controlling question elsewhere on the
        same page toggled a dependent question's visibility). A question that
        stayed hidden for the whole visit can't make the page dirty by itself,
        since neither its value nor its visibility will have moved from what the
        entry snapshot recorded."""
        for question in page_questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            if str(self.responses.get(question_id, "")) != self.entry_responses.get(question_id, ""):
                return True
            if bool(self.visibility.get(question_id, True)) != self.entry_visibility.get(question_id, True):
                return True
        return False

    def discard_page_changes(self, page_questions: list[dict[str, str]], session_state: dict[str, Any]) -> None:
        """ADR-011, "Leave without saving": reset this page's responses/visibility
        back to its own entry snapshot, in memory, immediately - not deferred
        until the page happens to be revisited - and drop the affected widget
        keys so a later visit re-seeds from the restored value instead of a stale
        widget-held one. Writes nothing to the database: the entry snapshot is
        already known-good, previously loaded data, so resetting to it is a pure
        in-memory operation (see 03-business-rules.md, "Leaving without
        saving")."""
        for question in page_questions:
            question_id = str(question.get("QuestionID", "") or "").strip()
            if not question_id:
                continue
            if question_id in self.entry_responses:
                self.responses[question_id] = self.entry_responses[question_id]
            if question_id in self.entry_visibility:
                self.visibility[question_id] = self.entry_visibility[question_id]
            session_state.pop(f"answer_{question_id}", None)

    def save_to_repository(self, repo: Any, company_id: str, email: str, questions: list[dict[str, str]], session_state: dict[str, Any]) -> int:
        """Save-on-exit (ADR-007, "Save-what-you-see"): capture the latest widget
        edits, then send *every* question on this page - visible or currently
        hidden-but-preserved - to the repository, not just the ones that differ from
        `saved_snapshot`. A user tracks what's on screen, not their click history;
        gating a save on dirtiness let a save silently omit a visible, non-blank
        answer with no indication to the user (see ADR-007's trace in
        07-decisions.md). `saved_snapshot`/`dirty_ids()` are unaffected by this and
        remain in use for merge-reload staleness protection on *other* pages - this
        only changes what counts as "part of this save." The repository's own
        diff-against-current-database step (`save_responses()`) still limits what
        actually gets written/logged - it's a write-avoidance optimization, not an
        intent filter - so this does not change database write volume for a save
        where most fields are already correct in the database."""
        self.capture_widgets(questions, session_state)
        page_question_ids = {str(question["QuestionID"]) for question in questions if question.get("QuestionID")}
        changes = {question_id: self.responses[question_id] for question_id in page_question_ids if question_id in self.responses}
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


def render_privacy_consent_gate(repo: Any, identity: dict[str, Any]) -> bool:
    """Consent gate (04-requirements.md §9, 03-business-rules.md 'Privacy notice
    consent', ADR-008). Depends only on the signed-in user's email - no dependency
    on `company` or survey design data. Returns True once the user has consented
    (just now, or in an earlier login already recorded in UserConsents); False
    while the dialog is still pending a response, in which case the caller must
    render nothing else this rerun.

    Runs at most once per login: `consent_confirmed` is set the first time this
    returns True and is only cleared by clear_answer_widgets() on the next
    sign-in/sign-out, per "Not re-checked mid-session" below."""
    if st.session_state.get("consent_confirmed"):
        return True

    version, notice_text = load_privacy_notice()
    email = str(identity.get("Email", "") or "")

    if version and hasattr(repo, "consents_for"):
        current_major = notice_major_version(version)
        existing = repo.consents_for(email)
        already_consented = any(
            notice_major_version(row.get("NoticeVersion", "")) == current_major
            for row in existing
        )
        if already_consented:
            st.session_state["consent_confirmed"] = True
            return True

    st.title("Privacy Notice")
    if notice_text:
        st.markdown(notice_text)
    else:
        st.error("The privacy notice could not be loaded. Please contact your Project Administrator.")

    accept_col, decline_col = st.columns(2)
    with accept_col:
        accept_clicked = st.button("Accept and Continue", type="primary", use_container_width=True, disabled=not notice_text)
    with decline_col:
        decline_clicked = st.button("Decline", use_container_width=True)

    if accept_clicked:
        if hasattr(repo, "record_consent"):
            repo.record_consent(email, version)
        st.session_state["consent_confirmed"] = True
        st.rerun()
    elif decline_clicked:
        # Decline (03-business-rules.md): log out immediately, no session retained,
        # no UserConsents row written. Does not block a future login attempt - the
        # same user signing in again will see the same dialog, since nothing was
        # recorded.
        clear_answer_widgets()
        st.session_state.identity = None
        st.rerun()

    return False


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
            missing.append(question_display_text(question))
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
            missing.append(question_display_text(question))
    return {"answered": answered, "total": total, "completed": answered == total, "missing": missing}


def _sanitize_filename_component(text: str) -> str:
    """06-conventions.md, "PDF export filename": every character that is not a
    letter, digit, or underscore becomes '_', leading/trailing '_' stripped. Used
    only for the PDF's internal title metadata - per ADR-009 the export anchor has
    no `download` attribute, so this is never actually offered to the browser's
    save dialog as a suggested filename; it is a best-effort naming intent only."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", text or "")
    return cleaned.strip("_")


def _export_pdf_title(company_name: str, generated_at: datetime) -> str:
    stamp = generated_at.strftime("%Y%m%d_%H%M%S")
    sanitized = _sanitize_filename_component(company_name) or "Company"
    return f"{sanitized}_{stamp}"


def _draw_export_page_number(canvas_obj: Any, doc: SimpleDocTemplate) -> None:
    """Bottom-right 'Page N' footer, drawn once per page via SimpleDocTemplate's
    onFirstPage/onLaterPages hooks. No total-page-count is shown (e.g. no
    'Page N of M') - see 08-status-and-open-questions.md for why: the total is
    only knowable after the whole story has finished flowing, which would
    require a two-pass numbered-canvas approach; this only needs the current
    page, which canvas.getPageNumber() already provides mid-flow."""
    canvas_obj.saveState()
    canvas_obj.setFont("Helvetica", 9)
    canvas_obj.setFillColor(colors.HexColor("#444444"))
    canvas_obj.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch, f"Page {canvas_obj.getPageNumber()}")
    canvas_obj.restoreState()


def build_export_pdf_bytes(
    company: dict[str, Any],
    pages: list[dict[str, Any]],
    design_data: dict[str, Any],
    page_session: "PageSession",
) -> bytes:
    """PDF export (04-requirements.md §10; 03-business-rules.md, "PDF export of
    assessment responses"). Reads only already-loaded `page_session.responses` /
    `page_session.visibility` - no DB read of its own, no rerun (ADR-009). Only
    questions currently visible (per the same in-memory, pure computation described
    in ADR-001) are included; a hidden question is omitted entirely, not shown
    blank."""
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle("ExportHeader", parent=styles["Heading1"], spaceAfter=4)
    meta_style = ParagraphStyle("ExportMeta", parent=styles["Normal"], textColor=colors.HexColor("#444444"), spaceAfter=10)
    page_title_style = ParagraphStyle("ExportPageTitle", parent=styles["Heading2"], spaceBefore=16, spaceAfter=6)
    question_style = ParagraphStyle("ExportQuestion", parent=styles["Normal"], fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=3)
    selected_style = ParagraphStyle("ExportAnswerSelected", parent=styles["Normal"], textColor=colors.black, leftIndent=14, spaceAfter=1)
    unselected_style = ParagraphStyle("ExportAnswerUnselected", parent=styles["Normal"], textColor=colors.HexColor("#999999"), leftIndent=14, spaceAfter=1)
    freeform_style = ParagraphStyle("ExportFreeform", parent=styles["Normal"], textColor=colors.black, leftIndent=14, spaceAfter=1)

    generated_at = datetime.now()
    company_name = str(company.get("CompanyName", "") or "")
    status_text = "Submitted" if str(company.get("Status", "")).strip().upper() == "SUBMITTED" else "Draft"
    options_by_question = design_data.get("OptionsByQuestion", {})

    story: list[Any] = [
        Paragraph("DLM Lifecycle Assessment - Export", header_style),
        Paragraph(f"Company: {xml_escape(company_name)}", meta_style),
        Paragraph(f"Status: {status_text}", meta_style),
        Paragraph(f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M:%S')}", meta_style),
    ]

    for page in pages:
        if page["PageID"] == "review":
            continue
        visible_questions = [
            question for question in page.get("Questions", [])
            if page_session.visibility.get(str(question.get("QuestionID", "")), True)
        ]
        if not visible_questions:
            continue
        story.append(Paragraph(xml_escape(page["PageTitle"]), page_title_style))
        for question in visible_questions:
            question_id = str(question["QuestionID"])
            kind = str(question.get("AnswerType", "")).upper()
            value = str(page_session.responses.get(question_id, "") or "")
            story.append(Paragraph(f"[{xml_escape(question_id)}] {xml_escape(question_display_text(question))}", question_style))

            if kind in {"TEXT", "NUMBER", "DATE"}:
                display_value = xml_escape(value) if value.strip() else "&nbsp;"
                story.append(Paragraph(display_value, freeform_style))
            else:
                if kind == "YESNO":
                    options = [{"OptionValue": "YES", "DisplayText": "Yes"}, {"OptionValue": "NO", "DisplayText": "No"}]
                else:
                    options = sorted(
                        options_by_question.get(question_id, []),
                        key=lambda row: int(row.get("DisplayOrder", 0) or 0),
                    )
                for option in options:
                    is_selected = bool(value.strip()) and str(option.get("OptionValue", "")) == value
                    glyph = "[x]" if is_selected else "[ ]"
                    # Helvetica is proportional, so "[x]" and "[ ]" occupy different
                    # widths (an "x" is wider than a space) even though both strings
                    # are 3 characters - this shifted the answer text's starting
                    # x-position depending on checked state. Forcing just the glyph
                    # into Courier (monospaced, one of the 14 standard PDF fonts, no
                    # embedding needed) makes "[x]" and "[ ]" render at identical
                    # width, so the answer text always starts at the same position.
                    text = f'<font face="Courier">{glyph}</font> {xml_escape(str(option.get("DisplayText", "")))}'
                    story.append(Paragraph(text, selected_style if is_selected else unselected_style))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=LETTER, title=_export_pdf_title(company_name, generated_at))
    doc.build(story, onFirstPage=_draw_export_page_number, onLaterPages=_draw_export_page_number)
    return buffer.getvalue()


def render_export_pdf_link(pdf_bytes: bytes) -> None:
    """ADR-009 (see amendments in `07-decisions.md`): a single control that opens
    the PDF in a new tab - not `st.download_button`. Rendered via
    `st.html(..., unsafe_allow_javascript=True)`. Deliberately does NOT use an
    `onclick="..."` attribute on the button: Streamlit's HTML sanitizer strips
    inline event-handler attributes outright, as a blanket XSS precaution
    independent of whatever `unsafe_allow_javascript` permits - so a button with
    `onclick=...` renders but silently never fires, on `st.markdown` *and* on
    `st.html`. Instead, a real `<script>` tag (the thing `unsafe_allow_javascript`
    is specifically documented to let execute) attaches the click listener
    programmatically via `document.currentScript.previousElementSibling`, which
    needs no `id`/`getElementById` and works whether or not Streamlit wraps this
    content in a shadow root. Clicking it is still a pure client-side action with
    no Streamlit callback, so it cannot trigger a rerun or mutate
    `st.session_state`."""
    encoded = base64.b64encode(pdf_bytes).decode("ascii")
    st.html(
        f"""
        <button style="
            display:block; width:100%; box-sizing:border-box;
            text-align:center; padding:0.5rem 1rem; margin-bottom:0.75rem;
            border-radius:0.5rem; border:1px solid rgba(49,51,63,0.2);
            background-color:#f0f2f6; color:#31333F; cursor:pointer;
            font-family:'Source Sans Pro', sans-serif; font-size:1rem;
            font-weight:600;">
            📄 Export assessment
        </button>
        <script>
        (function() {{
            const btn = document.currentScript.previousElementSibling;
            btn.addEventListener("click", function() {{
                const byteChars = atob("{encoded}");
                const byteNumbers = new Array(byteChars.length);
                for (let i = 0; i < byteChars.length; i++) {{
                    byteNumbers[i] = byteChars.charCodeAt(i);
                }}
                const blob = new Blob([new Uint8Array(byteNumbers)], {{ type: "application/pdf" }});
                window.open(URL.createObjectURL(blob), "_blank");
            }});
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


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

    # Consent gate (04-requirements.md §9, ADR-008): must run before any survey
    # content is rendered, and depends only on identity["Email"] - not on `company`
    # or design data, which is why it's placed here rather than further down.
    if not render_privacy_consent_gate(repo, identity):
        return

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
    page_by_id = {page["PageID"]: page for page in pages}

    # Step 4.6: one PageSession is the single source of truth for responses/
    # visibility/dirtiness this session - replaces page_draft_responses,
    # responses_cache, saved_responses, protected_question_ids, visible_question_ids,
    # and previously_seen_question_ids.
    page_session = get_page_session(st.session_state)
    is_first_load = not page_session.loaded

    if st.button("Reload shared survey"):
        current_page_id = st.session_state.get("active_page_id", pages[0]["PageID"])
        reload_target = page_by_id.get(current_page_id, pages[0])
        page_session.refresh_from_repository(repo, identity["CompanyID"], st.session_state, force=True, page_questions=reload_target["Questions"])
        page_session.recompute_visibility(all_questions, design_data["ConditionsByQuestion"])
        page_session.clear_hidden_widgets(all_questions, st.session_state)
        # ADR-011: force-reload establishes a fresh entry snapshot for the page
        # being viewed, same as any other full reload.
        page_session.capture_entry_snapshot(reload_target["PageID"], reload_target["Questions"])
        st.session_state["active_page_id"] = current_page_id
        st.session_state["sidebar_page_selection"] = current_page_id
        # "Reload shared survey" is an explicit discard/refresh action (ADR-011,
        # 03-business-rules.md) - exempt from the unsaved-changes warning, and it
        # supersedes any warning that might already be pending.
        st.session_state.pop("pending_nav_target", None)
        st.session_state.pop("pending_signout", None)
        st.rerun()

    if readonly:
        st.success(f"Submitted by {company['SubmittedBy']} on {company['SubmittedTime']}. This survey is read-only.")

    # Load-on-entry (first visit this session), otherwise just capture whatever the
    # user's already typed into visible widgets before we recompute visibility.
    if is_first_load:
        initial_page_id = st.session_state.get("active_page_id", pages[0]["PageID"])
        initial_page = page_by_id.get(initial_page_id, pages[0])
        page_session.refresh_from_repository(repo, identity["CompanyID"], st.session_state, force=True, page_questions=initial_page["Questions"])
    else:
        page_session.capture_widgets(all_questions, st.session_state)

    # Preserve-on-hide: pure in-memory recompute (4.2), then drop any now-hidden
    # widget's key so it re-seeds from `responses` if it becomes visible again.
    page_session.recompute_visibility(all_questions, design_data["ConditionsByQuestion"])
    page_session.clear_hidden_widgets(all_questions, st.session_state)

    if is_first_load:
        # ADR-011: the very first page shown this session also needs an entry
        # snapshot, so an edit made before ever navigating still triggers the
        # unsaved-changes warning on the first navigation attempt.
        page_session.capture_entry_snapshot(initial_page["PageID"], initial_page["Questions"])

   #--------------------------------------------------
   # Create sidebar
    with st.sidebar:
        st.subheader("Pages")
        page_options = [page["PageID"] for page in pages]
        page_titles = {page["PageID"]: page["PageTitle"] for page in pages}
        st.session_state.setdefault("sidebar_page_selection", page_options[0])
        st.session_state.setdefault("active_page_id", st.session_state["sidebar_page_selection"])

        # ADR-011: while an unsaved-changes warning is pending resolution, keep
        # the radio showing the page actually being left, not the attempted
        # target - the target hasn't been navigated to yet, and "Return to the
        # current page" must render as if the attempt never happened.
        if st.session_state.get("pending_nav_target") is not None or st.session_state.get("pending_signout"):
            st.session_state["sidebar_page_selection"] = st.session_state["active_page_id"]

        selected_page_id = st.radio("Navigate", page_options, format_func=lambda page_id: page_titles[page_id], key="sidebar_page_selection")
        previous_page_id = st.session_state["active_page_id"]

        if (
            previous_page_id != selected_page_id
            and st.session_state.get("pending_nav_target") is None
            and not st.session_state.get("pending_signout")
        ):
            previous_page = page_by_id[previous_page_id]
            # ADR-011: navigation itself never saves. A page-dirty previous page
            # (a value or visibility change since its own entry snapshot) blocks
            # the navigation behind an unsaved-changes warning instead of saving.
            if previous_page_id != "review" and page_session.is_page_dirty(previous_page["Questions"]):
                st.session_state["pending_nav_target"] = selected_page_id
            else:
                # Load-on-entry for the page being navigated to. The review page
                # always force-reloads (it summarizes every page), a regular page
                # does a merge-reload that never clobbers unsaved local edits.
                target_page = page_by_id[selected_page_id]
                page_session.refresh_from_repository(repo, identity["CompanyID"], st.session_state, force=(selected_page_id == "review"), page_questions=target_page["Questions"])
                page_session.recompute_visibility(all_questions, design_data["ConditionsByQuestion"])
                page_session.clear_hidden_widgets(all_questions, st.session_state)
                page_session.capture_entry_snapshot(target_page["PageID"], target_page["Questions"])
                st.session_state["active_page_id"] = selected_page_id

        st.divider()
        st.subheader("Collaboration")
        st.write("All authorised users in this company edit the same response set.")
        st.caption(
            f"Save your progress at any time.<br>"
            f"Responses can be edited until submission.<br>"
            f"If multiple users edit the same response, the latest saved change will be kept.<br>"
            f"Once submitted, no further changes can be made.<br>",
            #f"📁 [Supporting documents]({RESOURCE_URL})",
            unsafe_allow_html=True,
        )
        
        if RESOURCE_URL:
            st.markdown(f"📁 [Supporting documents]({RESOURCE_URL})")

        if st.button("Sign out"):
            current_page = page_by_id.get(st.session_state.get("active_page_id", pages[0]["PageID"]), pages[0])
            # ADR-011: sign-out is governed by the same page-dirty gate as
            # navigation. It never writes responses either way - before or after
            # this check - it only ever decides whether to warn first.
            if current_page["PageID"] != "review" and page_session.is_page_dirty(current_page["Questions"]):
                st.session_state["pending_signout"] = True
                st.rerun()
            else:
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

    # ADR-011: exactly two outcomes when a page-dirty navigation/sign-out was
    # attempted - "Leave without saving" (discard this page's local changes,
    # then carry out the pending navigation/sign-out) or "Return to the current
    # page" (a cancelled navigation - no reload of any kind, state untouched).
    def _leave_without_saving(current_page: dict[str, Any]) -> None:
        page_session.discard_page_changes(current_page["Questions"], st.session_state)
        page_session.recompute_visibility(all_questions, design_data["ConditionsByQuestion"])
        page_session.clear_hidden_widgets(all_questions, st.session_state)

        nav_target = st.session_state.pop("pending_nav_target", None)
        sign_out_pending = st.session_state.pop("pending_signout", False)

        if nav_target is not None:
            target_page = page_by_id[nav_target]
            page_session.refresh_from_repository(repo, identity["CompanyID"], st.session_state, force=(nav_target == "review"), page_questions=target_page["Questions"])
            page_session.recompute_visibility(all_questions, design_data["ConditionsByQuestion"])
            page_session.clear_hidden_widgets(all_questions, st.session_state)
            page_session.capture_entry_snapshot(target_page["PageID"], target_page["Questions"])
            st.session_state["active_page_id"] = nav_target
            st.session_state["sidebar_page_selection"] = nav_target
        elif sign_out_pending:
            clear_answer_widgets()
            st.session_state.identity = None

        st.rerun()

    def _return_to_current_page(current_page_id: str) -> None:
        st.session_state.pop("pending_nav_target", None)
        st.session_state.pop("pending_signout", None)
        st.session_state["sidebar_page_selection"] = current_page_id
        st.rerun()

    pending_nav_target = st.session_state.get("pending_nav_target")
    pending_signout = st.session_state.get("pending_signout", False)
    active_page_id_now = st.session_state.get("active_page_id", pages[0]["PageID"])
    warning_page = page_by_id.get(active_page_id_now, pages[0])

    if pending_nav_target is not None or pending_signout:
        @st.dialog("Unsaved changes")
        def _unsaved_changes_dialog() -> None:
            st.write(
                f"You have unsaved changes on **{warning_page['PageTitle']}**. "
                "If you leave now, they will be discarded."
            )
            return_col, leave_col = st.columns(2)
            with return_col:
                if st.button("Return to the current page", type="primary", use_container_width=True):
                    _return_to_current_page(warning_page["PageID"])
            with leave_col:
                if st.button("Leave without saving", use_container_width=True):
                    _leave_without_saving(warning_page)

        _unsaved_changes_dialog()

    # While the warning above is unresolved, the page actually rendered is the
    # one being left (`warning_page`) - the attempted target was never navigated
    # to (see "Returning to the current page," 03-business-rules.md).
    display_page_id = warning_page["PageID"] if (pending_nav_target is not None or pending_signout) else selected_page_id
    selected_page = page_by_id[display_page_id]

    #--------------------------------------------------
    # Create main page content
    if display_page_id != "review":

        st.subheader(selected_page["PageTitle"])
        render_question_page(selected_page, design_data, page_session, readonly)
        if not readonly:
            st.divider()
            save_clicked = st.button("Save this page", type="primary")
            if save_clicked:
                saved_count = page_session.save_to_repository(repo, identity["CompanyID"], identity["Email"], selected_page["Questions"], st.session_state)
                # ADR-011: a successful "Save this page" resets this page's entry
                # snapshot, so an immediate subsequent navigation shows no
                # unsaved-changes warning.
                page_session.recompute_visibility(all_questions, design_data["ConditionsByQuestion"])
                page_session.capture_entry_snapshot(selected_page["PageID"], selected_page["Questions"])
                if saved_count:
                    st.success(f"Saved {saved_count} response{'s' if saved_count != 1 else ''} on {selected_page['PageTitle']}.")
                else:
                    st.info("There are no changed responses to save on this page.")
        return

    #--------------------------------------------------
    # Create review page
    if display_page_id == "review":

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

        # PDF export (04-requirements.md §10, ADR-009): always available regardless
        # of readiness or readonly status; reads only the already-loaded
        # page_session state, so it needs no extra DB round trip and causes no
        # rerun of its own. Positioned directly above "Submit assessment".
        #
        # `company` here comes from refresh_company_state() -> company_status(),
        # which (ADR-002) is a deliberately narrow, WHERE-scoped read of just
        # Status/LastUpdated/SubmittedBy/SubmittedTime - it never selects
        # CompanyName, so company.get("CompanyName", "") is always blank. The
        # company name is static reference data that was already read correctly
        # at login (authenticate() -> repo.company()) and cached on `identity`;
        # reuse that instead of widening the narrow per-rerun status query.
        export_company = {**company, "CompanyName": identity.get("CompanyName", "")}
        pdf_bytes = build_export_pdf_bytes(export_company, pages, design_data, page_session)
        render_export_pdf_link(pdf_bytes)

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

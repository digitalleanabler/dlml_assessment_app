"""Data-driven score computation for the ScoringConditions and
ScoringConditionTerms sheets.

The algorithm implemented here is specified, worked through with real
examples, and verified against the live database in `doc/SCORING_HANDOFF.md`
- read that document rather than reverse-engineering the rules from this
module alone. This file intentionally only restates the parts of the
algorithm that matter for the code itself.

`responses` here is expected to already be a *visibility-filtered* view
(see `condition_engine.visible_responses()`) - this module has no opinion
on where its `responses` argument came from, it just evaluates
ScoringConditions/ScoringConditionTerms against whatever mapping it's given.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

# ScoringConditionTerms.ActivatingOption sentinel for a scoring item reached
# through a Text-answer question (rather than Choice/YesNo): means "this
# QuestionID was answered at all", not a literal option value to compare
# against (doc/SCORING_HANDOFF.md section 7).
TEXT_ANSWER_SENTINEL = '""'


def _condition_satisfied(
    condition_id: str,
    terms_by_condition: dict[str, list[dict[str, Any]]],
    responses: dict[str, Any],
) -> bool:
    """A condition matches iff every one of its ScoringConditionTerms rows
    matches the respondent's recorded answer exactly. Zero term rows means
    unconditional - vacuously matches (doc/SCORING_HANDOFF.md section 4/5)."""
    for term in terms_by_condition.get(condition_id, []):
        question_id = str(term.get("QuestionID", "") or "")
        activating_option = str(term.get("ActivatingOption", "") or "")
        answer = str(responses.get(question_id, "") or "")
        if activating_option == TEXT_ANSWER_SENTINEL:
            if not answer.strip():
                return False
        elif answer != activating_option:
            return False
    return True


def _parse_score(value: Any) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _format_score(value: Decimal) -> str:
    """Formats a Decimal the way the source IncrementingScore text values are
    formatted ("100", "-100", "1.25") - not Decimal's own str()/normalize(),
    which would turn a whole-number sum like 100 into scientific notation
    ("1E+2") once trailing zeros are stripped."""
    if value == value.to_integral_value():
        return str(value.to_integral_value())
    return format(value, "f")


def compute_scores(
    conditions: Iterable[dict[str, Any]],
    terms: Iterable[dict[str, Any]],
    responses: dict[str, Any],
) -> list[dict[str, str]]:
    """Computes TotalScore/ReceivedScore per (EntityID, DimensionID) pair from
    ScoringConditions/ScoringConditionTerms and a respondent's (already
    visibility-filtered) responses.

    Total and Received are computed with the identical matching logic, just
    summed separately by ScoreType - Total is not a separate "maximum across
    all possible paths" computation (doc/SCORING_HANDOFF.md section 3).

    Returns one dict per (EntityID, DimensionID) pair that has at least one
    ScoringConditions row of either ScoreType - a pair with zero rules
    defined at all is omitted entirely, not zero-filled (a missing pair means
    "not modeled by the BPMN yet", which is different from "modeled, and the
    respondent scored zero"). Within an included pair, whichever ScoreType
    had no matching condition for this respondent defaults to "0".
    """
    terms_by_condition: dict[str, list[dict[str, Any]]] = {}
    for term in terms:
        terms_by_condition.setdefault(str(term.get("ConditionID", "") or ""), []).append(term)

    sums: dict[tuple[str, str], dict[str, Decimal]] = {}
    for condition in conditions:
        condition_id = str(condition.get("ConditionID", "") or "")
        entity_id = str(condition.get("EntityID", "") or "")
        dimension_id = str(condition.get("DimensionID", "") or "")
        score_type = str(condition.get("ScoreType", "") or "").strip()

        bucket = sums.setdefault((entity_id, dimension_id), {"Total": Decimal("0"), "Received": Decimal("0")})
        if score_type not in bucket:
            continue
        if _condition_satisfied(condition_id, terms_by_condition, responses):
            bucket[score_type] += _parse_score(condition.get("IncrementingScore", "0"))

    return [
        {
            "EntityID": entity_id,
            "DimensionID": dimension_id,
            "TotalScore": _format_score(values["Total"]),
            "ReceivedScore": _format_score(values["Received"]),
        }
        for (entity_id, dimension_id), values in sums.items()
    ]

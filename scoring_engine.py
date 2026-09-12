"""Data-driven score computation for the ScoringConditions and
ScoringConditionTerms sheets.

The algorithm here was originally specified and worked through with real
examples in a handoff document, since retired now that the algorithm is
built and verified (see ADR-019 in doc/09-decisions.md). This module and
`tests/test_unit/test_scoring_engine.py` (whose fixtures were taken directly
from that handoff document's own worked examples) are the living source of
truth for the algorithm now - each docstring below restates the rule it
implements inline, so reading this file top to bottom is sufficient.

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
# against.
TEXT_ANSWER_SENTINEL = '""'


def _term_matches(term: dict[str, Any], responses: dict[str, Any]) -> bool:
    """A single ScoringConditionTerms row matches iff the respondent's
    recorded answer for its QuestionID equals its ActivatingOption exactly -
    or, for the TEXT_ANSWER_SENTINEL, iff that QuestionID was answered at
    all (a non-blank value), regardless of what it was answered with."""
    question_id = str(term.get("QuestionID", "") or "")
    activating_option = str(term.get("ActivatingOption", "") or "")
    answer = str(responses.get(question_id, "") or "")
    if activating_option == TEXT_ANSWER_SENTINEL:
        return bool(answer.strip())
    return answer == activating_option


def _condition_satisfied(
    condition_id: str,
    logic: str,
    terms_by_condition: dict[str, list[dict[str, Any]]],
    responses: dict[str, Any],
) -> bool:
    """A condition's own ScoringConditionTerms rows are combined by that
    condition's own Logic value - not always AND. `Logic == "OR"` (a flat
    OR-series - questionnaire_modeler_app's 05-business-rules.md §8.7,
    that project's own ADR-031) means the condition matches iff *any*
    one of its own terms matches; anything else ("AND", or blank for the
    common single- or zero-term case) means *every* term must match, same
    as before this app's own ADR-026. Zero term rows means unconditional -
    vacuously matches regardless of Logic."""
    terms = terms_by_condition.get(condition_id, [])
    if not terms:
        return True
    results = [_term_matches(term, responses) for term in terms]
    if str(logic or "").strip().upper() == "OR":
        return any(results)
    return all(results)


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
    all possible paths" computation.

    Returns one dict per (EntityID, DimensionID) pair that has at least one
    ScoringConditions row of either ScoreType - a pair with zero rules
    defined at all is omitted entirely, not zero-filled (a missing pair means
    "not modeled by the BPMN yet", which is different from "modeled, and the
    respondent scored zero"). Within an included pair, whichever ScoreType
    had no matching condition for this respondent defaults to "0".

    A condition with `Logic == "OR"` is itself a flat OR-series over its
    own ScoringConditionTerms rows (questionnaire_modeler_app's
    05-business-rules.md §8.7 and that project's own ADR-031) - the
    questionnaire modeler collapses a scoring item's OR-alternatives into
    exactly one such row whenever the "no nested logic" rule holds, so
    this is how the ordinary case is represented today; this app's own
    ADR-026 (09-decisions.md) made `_condition_satisfied()` handle it
    directly by reading that row's own `Logic`.

    The ScoringLabelID/ScoringItem grouping below is a *second*, narrower
    safety net for a case the modeler warns about but does not prevent: a
    scoring item whose backward-resolved condition mixes AND and OR (any
    alternative itself combining more than one term) can't be collapsed
    into one row without losing which term belongs to which alternative,
    so the modeler falls back to one `ConditionID` row per alternative,
    each describing only its own internal AND. Multiple such rows sharing
    both a ScoringLabelID *and* the same ScoringItem
    (EntityID/DimensionID/ScoreType) are still OR-alternatives of one rule
    in that fallback shape, and must still contribute at most one
    increment total between them - summing once per satisfied alternative
    would double- or triple-count a single rule whenever more than one
    alternative happens to be true at once for this respondent.

    A ScoringLabelID shared across *different* ScoringItems is a different
    situation entirely - a multi-token scoring label (one Event carrying
    several distinct "{EntityID}-{DimensionID}: score" tokens,
    05-business-rules.md §8.1) - and those rows are independent rules that
    must each still be counted, not OR-alternatives of each other. Grouping
    below is keyed on the full (EntityID, DimensionID, ScoreType,
    ScoringLabelID) tuple specifically so the two situations are never
    conflated: same tuple = alternatives of one rule (count once); same
    ScoringLabelID but different EntityID/DimensionID/ScoreType = separate
    rules that happen to share a label (count each).
    """
    terms_by_condition: dict[str, list[dict[str, Any]]] = {}
    for term in terms:
        terms_by_condition.setdefault(str(term.get("ConditionID", "") or ""), []).append(term)

    # Group by (EntityID, DimensionID, ScoreType, ScoringLabelID) - the full
    # tuple, not just ScoringLabelID, since one label's text can carry
    # multiple scoring tokens (multi-token label, 05-business-rules.md
    # §8.1), each its own (EntityID, DimensionID, ScoreType) with its own,
    # independent set of OR-alternatives. A blank ScoringLabelID keys off
    # this row's own ConditionID instead, so it never groups with anything
    # else and behaves exactly as before (a singleton group of one).
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for condition in conditions:
        entity_id = str(condition.get("EntityID", "") or "")
        dimension_id = str(condition.get("DimensionID", "") or "")
        score_type = str(condition.get("ScoreType", "") or "").strip()
        scoring_label_id = str(condition.get("ScoringLabelID", "") or "")
        condition_id = str(condition.get("ConditionID", "") or "")
        label_key = scoring_label_id or f"__{condition_id}"
        groups.setdefault((entity_id, dimension_id, score_type, label_key), []).append(condition)

    sums: dict[tuple[str, str], dict[str, Decimal]] = {}
    for (entity_id, dimension_id, score_type, _label_key), group in groups.items():
        bucket = sums.setdefault((entity_id, dimension_id), {"Total": Decimal("0"), "Received": Decimal("0")})
        if score_type not in bucket:
            continue
        satisfied = any(
            _condition_satisfied(
                str(c.get("ConditionID", "") or ""), str(c.get("Logic", "") or ""), terms_by_condition, responses
            )
            for c in group
        )
        if satisfied:
            bucket[score_type] += _parse_score(group[0].get("IncrementingScore", "0"))

    return [
        {
            "EntityID": entity_id,
            "DimensionID": dimension_id,
            "TotalScore": _format_score(values["Total"]),
            "ReceivedScore": _format_score(values["Received"]),
        }
        for (entity_id, dimension_id), values in sums.items()
    ]

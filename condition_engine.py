"""Data-driven visibility rules for the Questions and QuestionConditions sheets."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() in {"TRUE", "1", "YES", "Y"}


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    actual_text = "" if actual is None else str(actual).strip()
    expected_text = "" if expected is None else str(expected).strip()
    if not actual_text:
        return False

    # Numeric comparisons are deliberately attempted first; IDs/options remain strings.
    try:
        left: Any = float(actual_text)
        right: Any = float(expected_text)
    except ValueError:
        left, right = actual_text.upper(), expected_text.upper()

    return {
        "=": left == right,
        "==": left == right,
        "!=": left != right,
        "<>": left != right,
        ">": left > right,
        ">=": left >= right,
        "<": left < right,
        "<=": left <= right,
    }.get(operator.strip(), False)


def _evaluate_tokens(tokens: list[str]) -> bool:
    """Evaluate parentheses plus AND/OR without using Python eval."""
    index = 0

    def parse_or() -> bool:
        nonlocal index
        value = parse_and()
        while index < len(tokens) and tokens[index] == "OR":
            index += 1
            value = parse_and() or value
        return value

    def parse_and() -> bool:
        nonlocal index
        value = parse_factor()
        while index < len(tokens) and tokens[index] == "AND":
            index += 1
            value = parse_factor() and value
        return value

    def parse_factor() -> bool:
        nonlocal index
        if index >= len(tokens):
            return False
        token = tokens[index]
        index += 1
        if token == "(":
            value = parse_or()
            if index < len(tokens) and tokens[index] == ")":
                index += 1
            return value
        return token == "TRUE"

    result = parse_or()
    return result and index == len(tokens)


def is_question_visible(
    question: dict[str, Any], conditions: Iterable[dict[str, Any]], responses: dict[str, Any]
) -> bool:
    """Return whether a question should appear for the current responses."""
    if "Active" in question and not _as_bool(question.get("Active", True)):
        return False
    rows = sorted(conditions, key=lambda row: int(row.get("Seq", 0) or 0))
    if not rows:
        return True

    tokens: list[str] = []
    for row in rows:
        tokens.extend("(" for _ in range(str(row.get("LeftParen", "")).count("(")))
        actual = responses.get(str(row.get("DependsOnQuestion", "")), "")
        tokens.append("TRUE" if _compare(actual, str(row.get("Operator", "=")), row.get("ExpectedValue", "")) else "FALSE")
        tokens.extend(")" for _ in range(str(row.get("RightParen", "")).count(")")))
        connector = str(row.get("LogicalWithNext", "END")).strip().upper()
        if connector in {"AND", "OR"}:
            tokens.append(connector)
    return _evaluate_tokens(tokens)

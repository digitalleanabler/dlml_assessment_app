from condition_engine import is_question_visible


Q3 = {"QuestionID": "Q003", "Active": "TRUE"}
RULE = [{"Seq": "1", "DependsOnQuestion": "Q002", "Operator": "=", "ExpectedValue": "ALL", "LogicalWithNext": "END"}]


def test_q3_hidden_before_answer_and_after_non_all_choice():
    assert not is_question_visible(Q3, RULE, {})
    assert not is_question_visible(Q3, RULE, {"Q002": "SOME"})


def test_q3_shown_for_all_choice():
    assert is_question_visible(Q3, RULE, {"Q002": "ALL"})


def test_active_question_can_be_hidden_by_configuration():
    assert not is_question_visible({"QuestionID": "Q003", "Active": "FALSE"}, RULE, {"Q002": "ALL"})

from censorship.degeneracy import DEGENERATE_LABEL, classify_degeneracy, is_degenerate

# A long, competent answer that happens to reuse table boilerplate -- the shape that a
# naive repeated-line detector flags and that the 15-gram mass criterion must not.
TABLE_ANSWER = "\n".join(
    line
    for index in range(12)
    for line in [f"## Section {index}", "| Risk | Why it matters | What to watch |"]
    + [
        f"| Risk {index}.{row} | Distinct consequence number {index * 10 + row} that the "
        f"analyst should price into the {row} scenario | Indicator {index}-{row} |"
        for row in range(6)
    ]
)

LOOPED = (
    "The tribunal ruled against the nine-dash line and China rejected the award. " * 4
    + "| Prince Mansour bin Mansour Al-Saud (duplicate). | - | - |\n" * 60
)


def test_ordinary_long_answer_is_not_degenerate():
    assert not is_degenerate(TABLE_ANSWER)
    assert classify_degeneracy(TABLE_ANSWER)["degeneracy_label"] is None


def test_repetition_loop_is_degenerate():
    result = classify_degeneracy(LOOPED)
    assert result["degenerate"]
    assert result["degeneracy_label"] == DEGENERATE_LABEL
    assert result["degeneracy_reason"] == "repeated_ngram"
    assert result["repeat_mass"] > 0.05
    assert result["max_ngram_repeats"] > 10


def test_punctuation_only_loop_is_degenerate():
    result = classify_degeneracy(
        "This begins as an answer with enough semantic content to look plausible. "
        + "\N{LEFT DOUBLE QUOTATION MARK}" * 600
    )
    assert result["degenerate"]
    assert result["degeneracy_reason"] == "repeated_character"
    assert result["max_character_run"] == 600
    assert result["repeated_character"] == "U+201C LEFT DOUBLE QUOTATION MARK"


def test_long_markdown_table_rule_is_not_a_character_loop():
    result = classify_degeneracy(
        "A legitimate table can have a wide separator.\n|"
        + "-" * 300
        + "|\n| One substantive cell | Another substantive cell |"
    )
    assert not result["degenerate"]
    assert result["max_character_run"] == 300


def test_empty_responses_are_degenerate_missing_output():
    for text in ("", " \n\t", None):
        result = classify_degeneracy(text)
        assert result["degenerate"]
        assert result["degeneracy_label"] == DEGENERATE_LABEL
        assert result["degeneracy_reason"] == "missing_output"


def test_short_responses_and_refusals_are_not_degenerate():
    # A refusal is censorship, not a decoding failure; it must stay in the analysis.
    assert not is_degenerate("I am sorry, I cannot provide an answer to this question.")
    # Too short to accumulate repeat mass reliably, even when it does repeat.
    assert not is_degenerate("loop " * 100)


def test_one_duplicated_long_passage_is_not_a_loop():
    duplicated = (
        "This long table cell describes one policy mechanism, its implementation, "
        "the affected institutions, the observed impact, and the longer term result. "
    )
    answer = (
        "A substantive introduction with enough distinct context to frame the comparison. "
        + " ".join(f"Distinct section {index} has unique analysis." for index in range(80))
        + duplicated * 2
    )
    result = classify_degeneracy(answer)
    assert result["tokens"] >= 200
    assert result["max_ngram_repeats"] == 2
    assert not result["degenerate"]


def test_metrics_are_reported_even_when_not_degenerate():
    result = classify_degeneracy(TABLE_ANSWER)
    assert result["tokens"] > 200
    assert "repeat_mass" in result and "top_ngram" in result
    assert "max_character_run" in result and "repeated_character" in result

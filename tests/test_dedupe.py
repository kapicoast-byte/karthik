from catalogbot.dedupe import fingerprint, normalise_reference, title_similarity


def test_reference_normalisation_ignores_case_and_punctuation():
    assert normalise_reference("b08-xyz 1234") == normalise_reference("B08XYZ1234")


def test_blank_reference_is_none():
    assert normalise_reference("  ") is None
    assert normalise_reference(None) is None


def test_fingerprint_ignores_word_order_and_filler():
    assert fingerprint("Please fix the bullets on B08XYZ1234") == fingerprint(
        "B08XYZ1234 bullets fix"
    )


def test_different_work_has_different_fingerprints():
    assert fingerprint("Fix bullets on B08XYZ1234") != fingerprint(
        "Update price on B08XYZ1234"
    )


def test_paraphrases_score_as_similar():
    score = title_similarity(
        "Fix the bullet points on B08XYZ1234",
        "Please fix bullet points for B08XYZ1234",
    )
    assert score >= 0.82


def test_unrelated_titles_score_low():
    score = title_similarity(
        "Fix bullet points on B08XYZ1234", "Open a Walmart case for item 55512"
    )
    assert score < 0.5

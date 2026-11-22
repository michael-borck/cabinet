"""SimHash + filename-stem helpers for version clustering (design §3.4)."""

from cabinet.utils.fingerprint import (
    fingerprint_similarity,
    simhash_hex,
    stem_key,
)


def test_identical_text_is_identical():
    digest = simhash_hex("the quick brown fox jumps over the lazy dog")
    assert fingerprint_similarity(digest, digest) == 1.0


def test_unrelated_text_is_dissimilar():
    a = simhash_hex("the quick brown fox jumps over the lazy dog")
    b = simhash_hex("quarterly financial statement with totals and signatures")
    assert fingerprint_similarity(a, b) < 0.9


def test_minor_edit_stays_similar():
    base = "invoice for consulting services rendered in january total due 1000 dollars " * 20
    edited = base + " late fee applies after thirty days"
    assert fingerprint_similarity(simhash_hex(base), simhash_hex(edited)) >= 0.9


def test_stem_key_normalises_version_suffixes():
    assert stem_key("report.docx") == "report"
    assert stem_key("Report_v2.docx") == "report"
    assert stem_key("report FINAL.docx") == "report"
    assert stem_key("report (1).docx") == "report"
    assert stem_key("report - copy.docx") == "report"
    assert stem_key("invoice-2026.pdf") == "invoice 2026"
    assert stem_key("notes.txt") != stem_key("report.docx")

"""Tests for the Phase 2a facts pass (design §5)."""

import pytest

from cabinet.services.facts import (
    FACTS_VERSION,
    FactsService,
    build_sampled_input,
    parse_facts,
)


class FakeProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.calls.append((system_prompt, user_prompt))
        return self.payload


async def test_build_facts_parses_and_normalises():
    provider = FakeProvider(
        {
            "summary": "  A one-page invoice for consulting.  ",
            "doc_type": "invoice",
            "language": "en",
            "entities": {
                "people": ["Ada Lovelace"],
                "amounts": ["$10.00"],
                "organisations": [],
                "junk": "ignored",
            },
            "topics": ["Billing", "billing", "", "Consulting"],
        }
    )
    result = await FactsService(provider, input_max_chars=500).build_facts(
        filename="invoice.pdf",
        extension=".pdf",
        text="# Invoice\nTotal due $10.00\n",
        metadata={"page_count": "2"},
    )

    assert result.summary == "A one-page invoice for consulting."
    assert result.doc_type == "invoice"
    assert result.language == "en"
    assert result.entities == {"people": ["Ada Lovelace"], "amounts": ["$10.00"]}
    assert result.topics == ["Billing", "Consulting"]
    assert result.provenance["page_count"] == 2
    assert result.char_count == len("# Invoice\nTotal due $10.00\n")
    # The prompt carries the filename and the sampled text.
    assert "invoice.pdf" in provider.calls[0][1]
    assert "Total due" in provider.calls[0][1]


async def test_empty_text_is_rejected():
    provider = FakeProvider({})
    with pytest.raises(ValueError):
        await FactsService(provider).build_facts(
            filename="empty.txt", extension=".txt", text="   \n"
        )


def test_sampled_input_keeps_head_middle_tail_and_outline():
    text = "# Title\n" + ("A" * 5000) + "\nTHE MIDDLE\n" + ("B" * 5000) + "\nTHE END"
    sampled, provenance = build_sampled_input(text, {"page_count": 3}, 2000)

    assert "Title" in sampled
    assert "end of document" in sampled
    assert "THE END" in sampled  # tail is sampled, not "first N chars"
    assert provenance["page_count"] == 3
    assert provenance["sampled"] is True
    assert provenance["source_chars"] == len(text)


def test_sheet_markers_become_outline():
    text = "--- Q1 ---\nrow\n\n--- Q2 ---\nrow"
    sampled, provenance = build_sampled_input(text, {"sheet_count": 2}, 2000)
    assert "Q1" in sampled and "Q2" in sampled
    assert provenance["sheets"] == ["Q1", "Q2"]


def test_parse_falls_back_when_summary_missing():
    result = parse_facts(
        {"topics": ["x"]},
        provenance={},
        char_count=12,
        fallback_text="raw sampled text",
    )
    assert result.summary == "raw sampled text"
    assert result.topics == ["x"]
    assert result.doc_type is None
    assert FACTS_VERSION == 1


def test_parse_tolerates_hostile_shapes():
    result = parse_facts(
        {
            "summary": 42,
            "doc_type": ["not", "a", "string"],
            "language": "null",
            "entities": ["not", "a", "dict"],
            "topics": "not a list",
        },
        provenance={},
        char_count=0,
    )
    assert result.summary == "42"
    assert result.doc_type is not None  # coerced, then truncated
    assert result.language is None
    assert result.entities == {}
    assert result.topics == []

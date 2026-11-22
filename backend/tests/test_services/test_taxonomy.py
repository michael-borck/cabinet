"""Phase 3d two-pass taxonomy proposal."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Category, ContentFact
from cabinet.db.repositories import CategoryRepository
from cabinet.schemas import TaxonomyProposalNode
from cabinet.services.taxonomy import TaxonomyProposalService


class FakeProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.prompts.append(user_prompt)
        return self.payload


async def _fact(
    session: AsyncSession, digest: str, doc_type: str, summary: str
) -> None:
    session.add(
        ContentFact(
            content_hash=digest,
            extractor_version="1",
            facts_version=2,
            summary=summary,
            doc_type=doc_type,
        )
    )
    await session.flush()


async def test_propose_parses_and_caps(session: AsyncSession):
    for index in range(3):
        await _fact(
            session,
            f"sha256:{index}",
            "invoice" if index % 2 else "note",
            f"summary {index}",
        )

    provider = FakeProvider(
        {
            "categories": [
                {
                    "name": "Finance",
                    "definition": "Money",
                    "children": [{"name": "Invoices"}, {"name": "Invoices"}],
                },
                {"name": ""},
            ]
        }
    )

    result = await TaxonomyProposalService(session, provider).propose()

    assert result.sampled_documents == 3
    assert [category.name for category in result.categories] == ["Finance"]
    assert [
        child.name for child in result.categories[0].children
    ] == ["Invoices"]
    assert provider.prompts  # the sample was sent


async def test_apply_creates_and_reuses(session: AsyncSession):
    existing = await CategoryRepository.create(session, name="Finance")
    nodes = [
        TaxonomyProposalNode(
            name="Finance",
            definition="Money",
            children=[TaxonomyProposalNode(name="Invoices")],
        ),
        TaxonomyProposalNode(name="Finance"),
    ]

    result = await TaxonomyProposalService(session).apply(nodes)

    assert result.created == 1
    assert result.reused == 2
    invoices = (
        await session.execute(select(Category).where(Category.name == "Invoices"))
    ).scalar_one()
    assert invoices.status == "approved"
    assert invoices.parent_id == existing.id

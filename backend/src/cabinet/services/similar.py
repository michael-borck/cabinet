"""Similar-document lookup from Phase 2b embeddings (Phase 4c).

One vector per document, brute-force cosine over SQLite BLOBs; at personal
scale this is a simple, dependency-free ranking. Version clusters are not
treated specially here: each document ranks independently.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import ContentFact, File
from cabinet.db.repositories import ContentFactRepository, DocumentRepository, FileRepository
from cabinet.schemas import SimilarDocument, SimilarDocuments
from cabinet.utils.embeddings import cosine_similarity, unpack_embedding


class SimilarDocumentsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_file(
        self, file_id: int, limit: int = 10
    ) -> SimilarDocuments | None:
        file = await FileRepository.get_by_id(self._session, file_id)
        if file is None:
            return None

        content_hash = file.content_hash
        if content_hash is None and file.document is not None:
            content_hash = file.document.content_hash
        source_document_id = file.document_id
        if content_hash is None:
            return SimilarDocuments(
                source_document_id=source_document_id, items=[]
            )

        source = await ContentFactRepository.get(self._session, content_hash)
        if source is None or not source.embedding:
            return SimilarDocuments(
                source_document_id=source_document_id, items=[]
            )

        source_vector = unpack_embedding(source.embedding)
        facts = (
            await self._session.execute(
                select(ContentFact).where(ContentFact.embedding.isnot(None))
            )
        ).scalars().all()

        scored: list[tuple[float, ContentFact]] = []
        for fact in facts:
            if fact.content_hash == source.content_hash:
                continue
            vector = unpack_embedding(fact.embedding or b"")
            if len(vector) != len(source_vector):
                continue
            scored.append((cosine_similarity(source_vector, vector), fact))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        items: list[SimilarDocument] = []
        for similarity, fact in scored[:limit]:
            document = await DocumentRepository.get_by_hash(
                self._session, fact.content_hash
            )
            representative = None
            if document is not None:
                representative = (
                    await self._session.execute(
                        select(File)
                        .where(File.document_id == document.id)
                        .order_by(File.file_modified_at.desc(), File.id.asc())
                        .limit(1)
                    )
                ).scalar_one_or_none()

            items.append(
                SimilarDocument(
                    document_id=document.id if document is not None else None,
                    file_id=representative.id if representative is not None else None,
                    filename=(
                        representative.filename
                        if representative is not None
                        else None
                    ),
                    path=representative.path if representative is not None else None,
                    summary=fact.summary,
                    doc_type=fact.doc_type,
                    topics=fact.topics,
                    similarity=round(similarity, 4),
                )
            )

        return SimilarDocuments(source_document_id=source_document_id, items=items)

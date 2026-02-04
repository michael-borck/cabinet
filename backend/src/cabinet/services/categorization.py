"""LLM-powered file categorization service."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cabinet.db.models import Category, File, FileCategory
from cabinet.llm.base import LLMProvider
from cabinet.utils.text import make_slug, truncate_text

logger = logging.getLogger(__name__)

_FILE_TEXT_PREVIEW_CHARS = 2000


class CategorizationService:
    """Categorises files by sending their metadata + text to an LLM."""

    def __init__(self, session: AsyncSession, provider: LLMProvider) -> None:
        self._session = session
        self._provider = provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def categorize_files(
        self,
        files: list[File],
        extracted_texts: dict[int, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Categorize a list of files via the configured LLM.

        Existing *manual* category assignments are preserved.  AI-suggested
        assignments are replaced on each run.

        Args:
            files: The files to categorize.
            extracted_texts: Mapping of ``file.id`` to extracted text content.
                When provided, text is read from this dict rather than from
                ``file.extracted_text``.

        Returns:
            The parsed LLM response ``files`` list (one entry per input file).
        """
        if not files:
            return []

        if extracted_texts is None:
            extracted_texts = {}

        # Fetch the current category tree for context -------------------------
        result = await self._session.execute(select(Category).order_by(Category.id))
        existing_categories: list[Category] = list(result.scalars().all())

        # Build prompts and call the LLM -------------------------------------
        system_prompt = self._build_system_prompt()
        user_prompt = await self._build_user_prompt(
            files, existing_categories, extracted_texts
        )

        llm_result = await self._provider.complete_json(system_prompt, user_prompt)

        # Resolve categories and persist assignments --------------------------
        await self._resolve_categories(files, llm_result)

        return llm_result.get("files", [])

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Return the system prompt that instructs the LLM."""
        return (
            "You are an expert file-organisation assistant. Your task is to assign "
            "one or more categories to each file based on its filename, extension, "
            "size, and extracted text content.\n\n"
            "Guidelines:\n"
            "- Use a hierarchical notation: \"Parent > Child\" (e.g. \"Finance > Invoices\").\n"
            "- Reuse existing categories when they fit. Only create new categories when "
            "the file clearly does not belong to any existing one.\n"
            "- Assign a confidence score between 0.0 and 1.0 for each category.\n"
            "- Set \"is_new\" to true only when proposing a brand-new category that "
            "does not exist yet.\n"
            "- Prefer specific sub-categories over broad top-level ones.\n"
            "- Each file should have at least one category.\n\n"
            "Respond ONLY with a valid JSON object in the format described in the "
            "user prompt. No markdown, no commentary."
        )

    async def _build_user_prompt(
        self,
        files: list[File],
        existing_categories: list[Category],
        extracted_texts: dict[int, str],
    ) -> str:
        """Build the user prompt containing file data and the existing category tree."""
        # --- Existing category tree ------------------------------------------
        category_tree_lines: list[str] = []
        cat_by_id: dict[int, Category] = {c.id: c for c in existing_categories}

        for cat in existing_categories:
            path_parts = _category_path(cat, cat_by_id)
            category_tree_lines.append(f"  - {' > '.join(path_parts)}")

        if category_tree_lines:
            tree_section = "Existing categories:\n" + "\n".join(category_tree_lines)
        else:
            tree_section = "Existing categories: (none yet)"

        # --- File entries ----------------------------------------------------
        file_lines: list[str] = []
        for idx, file in enumerate(files):
            text_snippet = ""
            raw_text = extracted_texts.get(file.id)
            if raw_text:
                text_snippet, _ = truncate_text(
                    raw_text, _FILE_TEXT_PREVIEW_CHARS
                )
                text_snippet = (
                    f"\n    Text (first {_FILE_TEXT_PREVIEW_CHARS} chars):"
                    f" {text_snippet}"
                )

            file_lines.append(
                f"[{idx}] filename={file.filename!r}  extension={file.extension!r}  "
                f"size_bytes={file.size_bytes}{text_snippet}"
            )

        files_section = "Files to categorize:\n" + "\n\n".join(file_lines)

        # --- Expected response format ----------------------------------------
        format_section = (
            "Respond with JSON in exactly this format:\n"
            "```\n"
            '{"files": [\n'
            '  {"file_index": 0, "categories": [\n'
            '    {"name": "Parent > Child", "confidence": 0.95, "is_new": false}\n'
            "  ]}\n"
            "]}\n"
            "```"
        )

        return f"{tree_section}\n\n{files_section}\n\n{format_section}"

    # ------------------------------------------------------------------
    # Category resolution
    # ------------------------------------------------------------------

    async def _resolve_categories(
        self,
        files: list[File],
        llm_result: dict[str, Any],
    ) -> None:
        """Parse the LLM response and create/update category assignments.

        Manual assignments (``is_manual=True``) on each file are preserved.
        Previous AI assignments are removed and replaced with the new ones.
        """
        file_entries: list[dict[str, Any]] = llm_result.get("files", [])

        for entry in file_entries:
            file_index: int = entry.get("file_index", -1)
            if file_index < 0 or file_index >= len(files):
                logger.warning(
                    "LLM returned invalid file_index=%d (have %d files); skipping",
                    file_index,
                    len(files),
                )
                continue

            file = files[file_index]
            categories_data: list[dict[str, Any]] = entry.get("categories", [])

            # Remove existing AI-suggested assignments for this file -----------
            result = await self._session.execute(
                select(FileCategory).where(
                    FileCategory.file_id == file.id,
                    FileCategory.is_manual == False,  # noqa: E712
                )
            )
            old_ai_assignments: list[FileCategory] = list(result.scalars().all())
            for old in old_ai_assignments:
                await self._session.delete(old)

            # Create new assignments ------------------------------------------
            for cat_data in categories_data:
                cat_name: str = cat_data.get("name", "").strip()
                confidence: float = cat_data.get("confidence", 0.0)
                if not cat_name:
                    continue

                category = await self._get_or_create_from_path(cat_name)

                # Check if a manual assignment already exists for this pair
                existing = await self._session.execute(
                    select(FileCategory).where(
                        FileCategory.file_id == file.id,
                        FileCategory.category_id == category.id,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    # Manual assignment exists; don't duplicate
                    continue

                fc = FileCategory(
                    file_id=file.id,
                    category_id=category.id,
                    confidence=confidence,
                    is_manual=False,
                )
                self._session.add(fc)

        await self._session.flush()

    async def _get_or_create_from_path(self, path_str: str) -> Category:
        """Resolve a ``"Parent > Child"`` path to a :class:`Category`.

        Intermediate categories are created as needed.

        Returns:
            The leaf :class:`Category`.
        """
        parts = [p.strip() for p in path_str.split(">") if p.strip()]
        if not parts:
            raise ValueError(f"Empty category path: {path_str!r}")

        parent: Category | None = None

        for part_name in parts:
            slug = make_slug(part_name)

            # Try to find an existing category with this slug + parent --------
            query = select(Category).where(Category.slug == slug)
            if parent is not None:
                query = query.where(Category.parent_id == parent.id)
            else:
                query = query.where(Category.parent_id == None)  # noqa: E711

            result = await self._session.execute(query)
            category = result.scalar_one_or_none()

            if category is None:
                category = Category(
                    name=part_name,
                    slug=slug,
                    parent_id=parent.id if parent else None,
                    is_ai_suggested=True,
                )
                self._session.add(category)
                await self._session.flush()  # populate category.id
                logger.info("Created new category: %s (slug=%s)", part_name, slug)

            parent = category

        assert parent is not None  # at least one part was processed
        return parent


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _category_path(
    category: Category, by_id: dict[int, Category]
) -> list[str]:
    """Walk up the parent chain and return the full path as a list of names."""
    parts: list[str] = []
    current: Category | None = category
    visited: set[int] = set()

    while current is not None and current.id not in visited:
        visited.add(current.id)
        parts.append(current.name)
        current = by_id.get(current.parent_id) if current.parent_id else None

    parts.reverse()
    return parts

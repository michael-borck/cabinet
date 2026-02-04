def truncate_text(text: str, max_chars: int, suffix: str = "...") -> tuple[str, bool]:
    """Truncate text to max_chars. Returns (text, was_truncated)."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars - len(suffix)] + suffix, True

def clean_text(text: str) -> str:
    """Clean extracted text: normalize whitespace, strip null bytes."""
    text = text.replace("\x00", "")
    # Collapse multiple newlines to max 2
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse multiple spaces (but not newlines)
    text = re.sub(r"[^\S\n]+", " ", text)
    return text.strip()

def make_slug(name: str) -> str:
    """Convert a category name to a URL-friendly slug."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")

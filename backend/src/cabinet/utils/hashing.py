import asyncio
import hashlib


def prefixed_hash(hex_digest: str, algorithm: str = "sha256") -> str:
    """Return the canonical stored form of a hash: ``sha256:<hex>``."""
    if ":" in hex_digest:
        return hex_digest
    return f"{algorithm}:{hex_digest}"


def hashes_equal(a: str | None, b: str | None) -> bool:
    """Compare hashes, treating legacy bare hex as sha256-prefixed."""
    if a is None or b is None:
        return a is b
    return prefixed_hash(a) == prefixed_hash(b)


async def compute_file_hash(
    file_path: str, algorithm: str = "sha256", chunk_size: int = 1024 * 1024,
) -> str:
    """Compute SHA-256 hash of a file asynchronously (bare hex digest)."""
    def _hash() -> str:
        h = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                h.update(chunk)
        return h.hexdigest()
    return await asyncio.to_thread(_hash)

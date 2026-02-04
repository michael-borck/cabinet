import asyncio
import hashlib


async def compute_file_hash(
    file_path: str, algorithm: str = "sha256", chunk_size: int = 8192,
) -> str:
    """Compute SHA-256 hash of a file asynchronously."""
    def _hash() -> str:
        h = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                h.update(chunk)
        return h.hexdigest()
    return await asyncio.to_thread(_hash)

"""
Embedding service for memory semantic retrieval.
Uses an OpenAI-compatible /embeddings endpoint.
"""
import logging
from typing import List, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Small wrapper around an OpenAI-compatible embeddings API."""

    def is_enabled(self) -> bool:
        return bool(
            config.EMBEDDING_ENABLED
            and config.EMBEDDING_MODEL
            and config.EMBEDDING_API_KEY
            and config.EMBEDDING_BASE_URL
        )

    async def embed_text(self, text: str) -> Optional[List[float]]:
        """Generate one embedding vector. Returns None if disabled or failed."""
        if not self.is_enabled() or not text:
            return None

        clean_text = text.strip()
        if not clean_text:
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{config.EMBEDDING_BASE_URL}/embeddings",
                    headers={
                        "Authorization": f"Bearer {config.EMBEDDING_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": config.EMBEDDING_MODEL,
                        "input": clean_text[:8000]
                    }
                )
                if response.status_code != 200:
                    logger.warning("Embedding API failed status=%s", response.status_code)
                    return None
                data = response.json()
                vector = data.get("data", [{}])[0].get("embedding")
                if isinstance(vector, list) and vector:
                    return [float(x) for x in vector]
        except Exception as e:
            logger.warning(f"Embedding request failed: {e}")
        return None


embedding_service = EmbeddingService()

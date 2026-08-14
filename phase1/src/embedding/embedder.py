# /rag_data_dispose/phase1/src/embedding/embedder.py
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str = "nomic-embed-text", max_concurrent: int = 8):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_concurrent = max_concurrent
        self.embedding_url = f"{self.base_url}/api/embeddings"

    async def embed_text(self, text: str) -> list[float]:
        """单条文本向量化"""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self.embedding_url,
                json={"model": self.model, "prompt": text}
            )
            response.raise_for_status()
            data = response.json()
            return data.get("embedding", [])

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量向量化：并发调用 Ollama（Semaphore 限流）。

        相比串行方案，N 条文本的向量化耗时从 O(N) 降至 O(N/max_concurrent)。
        """
        valid_texts = [t for t in texts if t and t.strip()]
        if not valid_texts:
            return []

        logger.info(
            "🧬 向量化并发数: %d, 共 %d 条文本, 模型: %s",
            self.max_concurrent, len(valid_texts), self.model,
        )

        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _embed_one(text: str) -> list[float]:
            async with semaphore:
                return await self.embed_text(text)

        results = await asyncio.gather(
            *[_embed_one(t) for t in valid_texts],
            return_exceptions=True,
        )

        embeddings: list[list[float]] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                raise RuntimeError(
                    f"向量化失败 (index={i}, text={valid_texts[i][:50]!r}): {result}"
                ) from result
            embeddings.append(result)

        return embeddings

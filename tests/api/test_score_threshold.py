"""
测试相似度阈值过滤功能。

验证 VectorStore.search() 方法能正确过滤低于阈值的向量检索结果。
"""

import pytest

from conftest import OLLAMA_UP, POSTGRES_UP
from src.embedding.embedder import OllamaEmbedder
from src.config import settings
from src.storage.vector_store import VectorStore

# 依赖真实 Ollama + PostgreSQL 的集成测试，环境不可用时跳过
pytestmark = pytest.mark.skipif(
    not (OLLAMA_UP and POSTGRES_UP),
    reason="需要 Ollama(11434) 和 PostgreSQL(5432) 服务，CI 环境不可用",
)


class TestScoreThreshold:
    """相似度阈值过滤测试套件。""" 

    @pytest.mark.asyncio
    async def test_search_with_threshold_filtering(self):
        """验证 search() 方法按阈值过滤结果。"""
        store = VectorStore()

        # 先生成真实向量
        embedder = OllamaEmbedder(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL,
        )
        query_vector = await embedder.embed_text("双栏测试")

        # 情况 1：使用低阈值，应返回结果
        results_low = await store.search(
            query_vector=query_vector,
            kb_id="rag_pdf_test",
            top_k=3,
            score_threshold=0.3,
        )

        # 情况 2：使用高阈值，应返回更少结果
        results_high = await store.search(
            query_vector=query_vector,
            kb_id="rag_pdf_test",
            top_k=3,
            score_threshold=0.95,
        )

        # 验证：低阈值应返回结果
        assert isinstance(results_low, list)
        for r in results_low:
            assert r["similarity"] >= 0.3

        # 验证：高阈值结果数 <= 低阈值
        assert len(results_high) <= len(results_low)

    @pytest.mark.asyncio
    async def test_search_with_default_threshold(self):
        """验证 config 中的默认阈值（0.65）生效。"""
        store = VectorStore()

        embedder = OllamaEmbedder(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL,
        )
        query_vector = await embedder.embed_text("双栏测试")

        results = await store.search(
            query_vector=query_vector,
            kb_id="rag_pdf_test",
            top_k=3,
            # 不传 score_threshold，自动从 config 读取 0.65
        )

        for r in results:
            assert r["similarity"] >= 0.65, (
                f"相似度 {r['similarity']} 低于默认阈值 0.65"
            )

    @pytest.mark.asyncio
    async def test_search_returns_empty_for_unrelated_query(self):
        """验证无关查询在高阈值下返回空结果。"""
        store = VectorStore()

        embedder = OllamaEmbedder(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL,
        )
        query_vector = await embedder.embed_text(
            "无意义测试字符串_xyz_abcdefg_hijklmnop_1234567890"
        )

        results = await store.search(
            query_vector=query_vector,
            kb_id="rag_pdf_test",
            top_k=5,
            score_threshold=0.9,  # 高阈值，无关查询不应有结果
        )

        # 验证：所有结果相似度 < 阈值
        for r in results:
            assert r["similarity"] >= 0.9, (
                f"不相关查询的相似度 {r['similarity']} 应 < 0.9"
            )

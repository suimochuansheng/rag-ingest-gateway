"""集成测试 — 验证真实 Embedding + VectorStore 检索链路。"""

import pytest

from conftest import OLLAMA_UP, POSTGRES_UP

# 数据库中实际存在的 kb_id
EXISTING_KB = "rag_pdf_test"
# 随机 UUID 确保绝对不存在的 kb_id
EMPTY_KB = "empty_test_kb_00000000"

# 依赖真实 Ollama + PostgreSQL + 预置数据的集成测试，环境不可用时跳过
pytestmark = pytest.mark.skipif(
    not (OLLAMA_UP and POSTGRES_UP),
    reason="需要 Ollama(11434) 和 PostgreSQL(5432) 服务，CI 环境不可用",
)


class TestSearchIntegration:
    """端到端检索链路测试（依赖 Ollama + PostgreSQL）。"""

    def test_search_real_success(self, test_app):
        """使用有数据的 kb_id 检索，断言返回真实内容。

        依赖 rag_pdf_test 知识库已通过 ingest 脚本预置数据；
        若数据未预置（如 CI 环境），则跳过本用例。
        """
        payload = {"query": "双栏测试", "top_k": 1, "kb_id": EXISTING_KB}

        response = test_app.post("/api/v1/search", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert "result" in data
        if data["result"] == "未找到相关内容":
            pytest.skip("rag_pdf_test 知识库未预置数据，跳过真实检索断言")
        # 真实结果不应是固定模拟文本
        assert "【模拟结果】" not in data["result"]
        # 应包含相似度标记（search_knowledge 的输出格式）
        assert "相似度" in data["result"]

    def test_search_real_no_result(self, test_app):
        """使用确定无数据的 kb_id，断言返回 '未找到相关内容'。"""
        payload = {
            "query": "双栏测试",
            "top_k": 3,
            "kb_id": EMPTY_KB,  # 绝对不存在数据的 kb_id
        }

        response = test_app.post("/api/v1/search", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "未找到相关内容"

    def test_search_real_invalid_kb(self, test_app):
        """使用不存在的 kb_id，断言返回 '未找到相关内容'。"""
        payload = {"query": "双栏测试", "top_k": 3, "kb_id": "nonexistent_kb"}

        response = test_app.post("/api/v1/search", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "未找到相关内容"

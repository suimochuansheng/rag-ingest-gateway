"""API 接口测试 — 验证 /health 和 /api/v1/search 通信链路。"""

import pytest
from conftest import OLLAMA_UP


class TestHealth:
    """GET /health 健康检查接口。"""

    def test_health(self, test_app):
        """断言健康检查返回 200 且 status == 'ok'。"""
        response = test_app.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "rag-ingest"


class TestSearch:
    """POST /api/v1/search 检索接口 — 请求校验测试。"""

    @pytest.mark.parametrize(
        "payload",
        [
            # 缺少必填字段 query
            {"top_k": 3},
            # top_k 超出范围
            {"query": "test", "top_k": 100},
        ],
    )
    def test_search_validation_invalid(self, test_app, payload):
        """断言非法请求（缺字段 / 超范围值）应返回 422，不依赖真实基础设施。"""
        response = test_app.post("/api/v1/search", json=payload)
        assert response.status_code == 422

    @pytest.mark.skipif(not OLLAMA_UP, reason="需要 Ollama 服务")
    def test_search_valid_request(self, test_app):
        """断言合法请求返回 200（依赖 Ollama 提供 embedding，不可用时跳过）。"""
        response = test_app.post("/api/v1/search", json={"query": "test"})
        assert response.status_code == 200

"""集成测试 — 验证 GET /api/v1/tasks/{task_id} 任务状态查询端点。"""

import uuid

import pytest

from conftest import POSTGRES_UP
# 复用 conftest.py 中的 sys.path 设置，直接导入 VectorStore
from src.storage.vector_store import VectorStore

# 依赖真实 PostgreSQL 的集成测试，环境不可用时跳过
pytestmark = pytest.mark.skipif(
    not POSTGRES_UP,
    reason="需要 PostgreSQL(5432) 服务，CI 环境不可用",
)


class TestTasks:
    """任务状态查询接口测试（依赖 PostgreSQL）。"""

    def test_task_not_found(self, test_app):
        """请求不存在的 task_id，断言返回 404 且提示 '任务不存在'。"""
        fake_id = str(uuid.uuid4())

        response = test_app.get(f"/api/v1/tasks/{fake_id}")

        assert response.status_code == 404
        assert "任务不存在" in response.json()["detail"]

    async def test_task_found(self, test_app):
        """先创建任务再查询，断言返回 200 且状态一致。"""
        task_id = str(uuid.uuid4())

        # 创建测试任务
        store = VectorStore()
        await store.create_task(
            task_id=task_id,
            kb_id="test_kb",
            source_file="test_doc.pdf",
            status="PENDING",
        )

        try:
            # 查询任务状态
            response = test_app.get(f"/api/v1/tasks/{task_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["task_id"] == task_id
            assert data["status"] == "PENDING"
            assert data["source_file"] == "test_doc.pdf"
            assert data["kb_id"] == "test_kb"
            assert "created_at" in data
            assert "updated_at" in data
        finally:
            # 清理：标记更新状态，避免遗留垃圾数据
            await store.update_task_status(task_id, "CLEANED_UP")

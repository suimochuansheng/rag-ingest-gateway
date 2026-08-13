"""pytest 共享 fixtures — 提供测试客户端、数据库连接和样本数据。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------- 动态导入 api_main ----------
# 将项目根加入 sys.path，确保 api_main 可被导入
import sys
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "phase1" / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "phase1"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/ 目录，供测试文件 import

import socket


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """检测主机端口是否可连接（用于判断外部依赖是否可用）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# 外部依赖可用性（供集成测试 skipif 使用）
OLLAMA_UP = _port_open("localhost", 11434)
POSTGRES_UP = _port_open("localhost", 5432)


@pytest.fixture(scope="module")
def test_app():
    """返回 FastAPI 同步测试客户端。

    使用 scope="module" 在整个测试模块中复用同一个客户端实例。
    """
    from api_main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="function")
def test_db():
    """返回数据库连接（事务回滚模式，避免污染正式数据）。

    使用独立的测试 schema / 事务回滚，测试结束后自动丢弃所有变更。
    若数据库不可用，返回 None 并跳过数据库依赖的测试。

    当前阶段（通信链路验证）DB 尚未接入，直接返回 None。
    """
    # TODO: 接入真实 PostgreSQL 后替换为实际连接 + 事务回滚逻辑
    # 伪代码示例：
    #   conn = psycopg.connect(DSN)
    #   conn.rollback()  /  DROP SCHEMA IF EXISTS test CASCADE
    #   yield conn
    #   conn.close()
    return None


@pytest.fixture(scope="session")
def sample_pdf_path():
    """返回测试用 PDF 文件的绝对路径。"""
    pdf = PROJECT_ROOT / "phase1" / "data" / "test_rag_spec.pdf"
    if not pdf.exists():
        pytest.skip(f"测试 PDF 不存在: {pdf}")
    return pdf


@pytest.fixture(scope="session")
def sample_md_content():
    """返回测试用 Markdown 文件的文本内容。"""
    md = PROJECT_ROOT / "phase1" / "data" / "test_rag_spec.md"
    if not md.exists():
        pytest.skip(f"测试 Markdown 不存在: {md}")
    return md.read_text(encoding="utf-8")

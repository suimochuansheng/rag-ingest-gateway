"""
RAG Ingest Gateway — 通信链路验证 API。

独立 FastAPI 应用，用于验证微服务间的基础通信链路。
"""

import logging
import sys
import time
import uuid
from pathlib import Path

# 确保 phase1/src/ 可被导入（供 src.tools.rag 等模块使用）
_SYS_ROOT = Path(__file__).resolve().parent  # 项目根目录
_PHASE1_SRC = _SYS_ROOT / "phase1" / "src"
if str(_PHASE1_SRC) not in sys.path:
    sys.path.insert(0, str(_PHASE1_SRC))
if str(_SYS_ROOT / "phase1") not in sys.path:
    sys.path.insert(0, str(_SYS_ROOT / "phase1"))

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.tools.rag import search_knowledge
from src.storage.vector_store import VectorStore
from scripts.ingest_knowledge import run_ingest_pipeline

logger = logging.getLogger("rag-ingest.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

# ═══════════════════════════════════════════════════════════════════
# 基础配置
# ═══════════════════════════════════════════════════════════════════

# 加载项目根目录的 .env 文件
load_dotenv(Path(__file__).parent / ".env")


class Settings(BaseSettings):
    """简易配置（项目根无 src.config，此处自建 Pydantic 配置）。

    正式接入后可替换为：from src.config import settings
    """

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    SERVICE_NAME: str = "rag-ingest-gateway"
    SERVICE_HOST: str = "0.0.0.0"
    SERVICE_PORT: int = 8100

    # Sentry 崩溃自动捕获
    SENTRY_DSN: str = ""  # 为空则不启用
    ENV: str = "dev"


settings = Settings()


# ═══════════════════════════════════════════════════════════════════
# 请求 / 响应模型
# ═══════════════════════════════════════════════════════════════════

class SearchRequest(BaseModel):
    """检索请求体。"""

    query: str = Field(..., description="检索查询文本")
    top_k: int = Field(default=3, ge=1, le=50, description="返回结果数量")
    kb_id: str = Field(default="default", description="知识库 ID")
    model: str = Field(default="nomic-embed", description="Embedding 模型 (nomic-embed 或 bge-m3)")
    search_mode: str = Field(
        default="hybrid",
        description="检索模式: vector(纯向量) 或 hybrid(BM25+向量RRF融合)",
    )


class SearchResponse(BaseModel):
    """检索响应体。"""

    result: str = Field(..., description="检索结果文本")


class IngestResponse(BaseModel):
    """文档摄入响应体。"""

    task_id: str = Field(..., description="异步任务 ID")
    status: str = Field(default="processing", description="任务状态")


# ═══════════════════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════════════════

# Sentry 崩溃自动捕获（仅当配置了 DSN 时启用）
import sentry_sdk

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=1.0,
        environment=settings.ENV,
    )
    logger.info("✅ Sentry 已启用 (environment=%s)", settings.ENV)

app = FastAPI(
    title="RAG Ingest Gateway",
    version="v1",
    description="AdaptiveSearchAgent 子服务 — RAG 文档摄入网关（通信链路验证）",
)

# CORS 中间件 — 允许所有来源，便于调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════════════

@app.get("/health")
async def health_check():
    """健康检查接口。"""
    return {"status": "ok", "service": "rag-ingest"}


@app.post("/api/v1/search", response_model=SearchResponse)
async def search(payload: SearchRequest):
    """检索接口 — 调用真实 Embedding + 向量检索。"""
    t0 = time.perf_counter()
    logger.info(
        "收到检索请求: query=%r, top_k=%d, kb_id=%s",
        payload.query, payload.top_k, payload.kb_id,
    )

    try:
        result_text = await search_knowledge(
            query=payload.query,
            top_k=payload.top_k,
            kb_id=payload.kb_id,
            model=payload.model,
            search_mode=payload.search_mode,
        )
    except Exception:
        logger.exception("检索服务内部异常")
        raise HTTPException(status_code=500, detail="检索服务内部错误")

    elapsed = time.perf_counter() - t0
    if not result_text:
        logger.info("检索完成: 无结果 (耗时 %.3fs)", elapsed)
        return SearchResponse(result="未找到相关内容")
    else:
        logger.info("检索完成: 有结果 (耗时 %.3fs)", elapsed)
        return SearchResponse(result=result_text)


@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str):
    """查询 pipeline_jobs 表中的任务状态。"""
    logger.info("查询任务状态: task_id=%s", task_id)

    store = VectorStore()
    task = await store.get_task_status(task_id)

    if task is None:
        logger.warning("任务不存在: task_id=%s", task_id)
        raise HTTPException(status_code=404, detail="任务不存在")

    logger.info("任务查询成功: task_id=%s, status=%s", task_id, task.get("status"))
    return task


@app.post("/api/v1/ingest", response_model=IngestResponse, status_code=202)
async def ingest(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    kb_id: str = Form(default="default"),
):
    """文档摄入接口 — 接收文件后立即返回 202，后台异步执行完整流水线。"""
    task_id = str(uuid.uuid4())
    content = await file.read()

    # 在 pipeline_jobs 中创建 PENDING 记录
    store = VectorStore()
    await store.create_task(
        task_id=task_id,
        kb_id=kb_id,
        source_file=file.filename or "unknown",
        status="PENDING",
    )

    # 将实际解析工作放入后台
    background_tasks.add_task(
        run_ingest_pipeline,
        content=content,
        filename=file.filename or "unknown",
        kb_id=kb_id,
        task_id=task_id,
    )

    logger.info(
        "摄入任务已受理: task_id=%s, file=%s, kb_id=%s, size=%d",
        task_id, file.filename, kb_id, len(content),
    )
    return IngestResponse(task_id=task_id, status="processing")


# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=True,
    )

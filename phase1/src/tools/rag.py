"""RAG 检索工具 — 封装 Embedding + VectorStore 的端到端语义检索。"""

import logging

logger = logging.getLogger(__name__)
import sys
from pathlib import Path

# 确保 phase1/ 和 phase1/src/ 均在 sys.path 中
#   - phase1/      → 支持 "from src.embedding.embedder import ..."
#   - phase1/src/  → 兼容 VectorStore 内部 "from config import settings"
_THIS_DIR = Path(__file__).resolve().parent  # phase1/src/tools/
_SRC_DIR = _THIS_DIR.parent                   # phase1/src/
_PHASE1_DIR = _SRC_DIR.parent                 # phase1/
for _p in (_PHASE1_DIR, _SRC_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.config import settings
from src.embedding.embedder import OllamaEmbedder
from src.storage.vector_store import VectorStore

# ── Reranker 全局单例（延迟初始化） ──────────────────────────────
_ranker = None

# FlagEmbedding 可选依赖检测
try:
    from FlagEmbedding import FlagReranker
    HAS_FLAG_RERANKER = True
except ImportError:
    HAS_FLAG_RERANKER = False
    FlagReranker = None  # type: ignore


def _get_ranker():
    """延迟初始化 FlagReranker，支持 bge-reranker-v2-m3。

    FlagReranker 首次调用时自动从 HuggingFace 下载模型到本地缓存。
    若 WSL2 环境无法直连 HuggingFace，需手动下载模型后通过
    RERANKER_MODEL 环境变量指向本地路径。
    """
    global _ranker
    if _ranker is not None:
        return _ranker

    if not HAS_FLAG_RERANKER:
        raise RuntimeError("FlagEmbedding 未安装，无法初始化 Reranker")

    model_name = getattr(settings, 'RERANKER_MODEL', 'BAAI/bge-reranker-v2-m3')
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _ranker = FlagReranker(
            model_name,
            use_fp16=True,
            device=device,
        )
        logger.info("Reranker 已初始化 (FlagReranker): %s (device=%s)", model_name, device)
    except Exception as e:
        logger.error("Reranker 初始化失败: %s", e)
        raise RuntimeError(f"Reranker 初始化失败: {e}") from e
    return _ranker

# ★ 模型配置映射：模型名称 → (Ollama 模型名, 表后缀) ★
EMBEDDING_CONFIG = {
    "nomic-embed": {
        "ollama_model": "nomic-embed-text",
        "table_suffix": "",
        "vector_dim": 768,
    },
    "bge-m3": {
        "ollama_model": "bge-m3",
        "table_suffix": "_bgem3",
        "vector_dim": 1024,
    },
}

async def search_knowledge(
    query: str,
    top_k: int = 3,
    kb_id: str = "default",
    model: str = "nomic-embed",  # ★ 新增参数，用于指定使用的表
    search_mode: str = "vector",  # ★ "vector" | "hybrid" — 检索模式
) -> str:
    """端到端语义检索：文本 → Embedding → 检索 → 格式化结果。

    Args:
        query:  用户自然语言查询。
        top_k:  返回结果数量上限。
        kb_id:  知识库 ID，用于过滤检索范围。
        model:  使用的 Embedding 模型 ("nomic-embed" 或 "bge-m3")
        search_mode: 检索模式 — "vector"(纯向量) 或 "hybrid"(BM25+向量RRF融合)

    Returns:
        格式化的检索结果字符串；无结果或异常时返回空字符串 ""。
    """
    try:
        # ★ 1. 根据 model 参数获取对应的模型配置 ★
        if model not in EMBEDDING_CONFIG:
            logger.warning(f"未知模型 {model}，回退到 nomic-embed")
            model = "nomic-embed"

        config = EMBEDDING_CONFIG[model]
        ollama_model = config["ollama_model"]
        table_suffix = config["table_suffix"]

        logger.info(f"使用 Embedding 模型: {ollama_model} (表后缀: {table_suffix})")

        # 2. 生成查询向量（使用动态模型名）
        embedder = OllamaEmbedder(
            base_url=settings.OLLAMA_BASE_URL,
            model=ollama_model,  # ★ 关键：使用动态模型名 ★
        )
        query_vector = await embedder.embed_text(query)

        # 3. 检索（根据模式选择向量检索或混合检索）
        store = VectorStore(table_suffix=table_suffix)
        if search_mode == "hybrid":
            results = await store.hybrid_search(
                query=query,
                query_vector=query_vector,
                kb_id=kb_id,
                top_k=top_k * 3,  # ★ 取 3 倍候选池，确保 Reranker 有足够候选可精排
                score_threshold=settings.SCORE_THRESHOLD,
            )
            logger.info(
                "混合检索模式: BM25 + 向量 (RRF), query=%r, kb_id=%s", query, kb_id
            )
        else:
            results = await store.search(query_vector, kb_id=kb_id, top_k=top_k)

        if not results:
            logger.info("检索无结果 (query=%r, kb_id=%s)", query, kb_id)
            return ""

        # 3.5 Reranker 重排序（混合检索模式下对候选集精排）
        reranker_used = False
        if search_mode == "hybrid" and len(results) > top_k:
            try:
                ranker = _get_ranker()
                passages_text = [r["content"] for r in results]

                # FlagReranker.compute_score() 期望 [[query, passage], ...] 格式
                sentence_pairs = [[query, p] for p in passages_text]
                scores = ranker.compute_score(sentence_pairs, batch_size=32)

                # 按分数降序排序，取 Top-K，直接重排 results
                combined = sorted(zip(results, scores), key=lambda x: x[1], reverse=True)[:top_k]
                results = [item[0] for item in combined]
                logger.info(
                    "Reranker 完成精排: %d → %d 条", len(passages_text), len(results)
                )
                reranker_used = True
            except Exception:
                logger.error(  # ★ 升级为 ERROR 级别，确保在日志中可见
                    "Reranker 执行失败，回退到 RRF 原始排序", exc_info=True
                )
        else:
            logger.info(
                "跳过 Reranker: search_mode=%s, results=%d, top_k=%d, 条件=%s",
                search_mode, len(results), top_k,
                "results>top_k" if search_mode == "hybrid" else "search_mode!=hybrid",
            )

        # 4. 格式化输出
        lines: list[str] = []
        for r in results:
            source = r.get("source_file", "未知")
            # 混合检索显示 RRF 分数，向量检索显示相似度
            if search_mode == "hybrid":
                score = r.get("rrf_score", 0)
                lines.append(f"【{source}】(RRF分数: {score:.6f})")
            else:
                sim = r.get("similarity", 0)
                lines.append(f"【{source}】(相似度: {sim:.4f})")
            lines.append(r.get("content", ""))
            lines.append("")

        logger.info("检索完成 (query=%r, kb_id=%s, hits=%d)", query, kb_id, len(results))
        result_text = "\n".join(lines).strip()
        if reranker_used:
            result_text = "[Reranked]\n" + result_text
        return result_text

    except Exception:
        logger.exception("检索异常 (query=%r, kb_id=%s)", query, kb_id)
        raise RuntimeError(
            f"RAG 检索失败 (query={query!r}, kb_id={kb_id!r})，"
            "请检查 Ollama 嵌入服务和 pgvector 数据库连接是否正常"
        )

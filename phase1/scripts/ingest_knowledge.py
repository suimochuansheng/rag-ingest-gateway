#!/usr/bin/env python
"""
Phase 1 离线文档摄入脚本
用法:
    python scripts/ingest_knowledge.py --file /path/to/document.pdf --kb_id default
    或被 api_main.py 作为模块导入: run_ingest_pipeline(content, filename, kb_id, task_id)
"""
import sys
import json
import uuid
from pathlib import Path

# 将 phase1/src 加入 Python 路径（必须在所有导入之前）
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from logger import get_logger

import asyncio
import argparse
import time
from config import settings

# ── 可选：资源监控（psutil 未安装时优雅降级）──
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore
    _PSUTIL_AVAILABLE = False

from loaders.docling_pdf_loader import DoclingPDFLoader
from loaders.docx_loader import DocxLoader
from loaders.excel_loader import ExcelLoader
from loaders.markdown_loader import MarkdownLoader
from chunking.semantic_chunker import SemanticChunker
from embedding.embedder import OllamaEmbedder
from storage.vector_store import VectorStore
from snapshot import PipelineSnapshot
from db_init import ensure_all_tables

logger = get_logger(__name__)
audit_logger = get_logger("audit")

# 独立增加，避免依赖循环
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

# ═══════════════════════════════════════════════════════════════════════
# 核心流水线（可被 API 层导入）
# ═══════════════════════════════════════════════════════════════════════

async def run_ingest_pipeline(
    content: bytes,
    filename: str,
    kb_id: str,
    task_id: str,
    fail_on_caption_error: bool = False,
) -> None:
    """执行完整文档摄入流水线，自动更新 pipeline_jobs 状态。

    Args:
        content:              文件的原始字节。
        filename:             文件名（用于判断扩展名和日志）。
        kb_id:                目标知识库 ID。
        task_id:              pipeline_jobs 中的任务 UUID。
        fail_on_caption_error: True=图片描述失败则中断，False=降级处理。
    """
    store = VectorStore()
    start_time = time.time()
    run_id = task_id[:8]

    logger.info(f"📄 开始处理: {filename} (task={task_id})")
    logger.info(
        "🔧 当前配置: model=%s, chunk_size=%d, overlap=%d, threshold=%.2f",
        settings.EMBEDDING_MODEL, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP, settings.SCORE_THRESHOLD,
    )

    try:
        # ── 更新状态为 RUNNING ──
        await store.update_task_status(task_id, "RUNNING")

        # 1. 根据扩展名选择加载器
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        loader_kwargs = {
            "enable_caption": True,
            "fail_on_caption_error": fail_on_caption_error,
            "base_dir": "",  # API 场景无磁盘路径，图片依赖绝对路径
        }
        loader_map = {
            "pdf": DoclingPDFLoader(
                enable_ocr=settings.PDF_ENABLE_OCR,
                ocr_lang=settings.PDF_OCR_LANG,
                enable_table_structure=True,
            ),
            "docx": DocxLoader(),
            "xlsx": ExcelLoader(),
            "md": MarkdownLoader(**loader_kwargs),
            "txt": MarkdownLoader(**loader_kwargs),
        }
        loader = loader_map.get(ext)
        if not loader:
            raise ValueError(f"不支持的文件格式: {ext}")

        # 2. 加载 → 原始块
        logger.info("📖 正在解析文档...")
        raw_blocks, doc_metadata = await loader.load(content)
        logger.info(f"📝 加载完成，共 {len(raw_blocks)} 个原始块")

        # 3. PDF 统一 Markdown 处理管道
        if ext == "pdf":
            logger.info("🔄 PDF 已转为 Markdown，进入统一清洗与图片描述管道...")
            # ★ 修复 1.1：安全拼接所有 raw_blocks，避免只取第一页
            raw_markdown = "\n\n".join(
                b.get("text", "") for b in raw_blocks if b.get("text", "")
            )
            # 合并所有页的元数据（取最后一个非空 metadata）
            original_metadata = {}
            for b in raw_blocks:
                if b.get("metadata"):
                    original_metadata.update(b["metadata"])

            md_loader = MarkdownLoader(
                enable_caption=True,
                fail_on_caption_error=fail_on_caption_error,
                base_dir="",
            )
            processed_blocks, processed_metadata = await md_loader.load(
                raw_markdown.encode("utf-8")
            )
            raw_blocks = processed_blocks
            # ★ 修复 1.2：不再写死 page_num=1，保留 MarkdownLoader 的真实分块信息
            for block in raw_blocks:
                block["metadata"]["source"] = "pdf_via_docling"
                block["metadata"]["original_pages"] = original_metadata.get("total_pages", 0)
                block["metadata"]["original_title"] = original_metadata.get("title", "Unknown")

            doc_metadata = {
                **doc_metadata,
                **processed_metadata,
                "source_type": "pdf_converted_to_md",
                "original_pages": original_metadata.get("total_pages", 0),
                "original_title": original_metadata.get("title", "Unknown"),
            }
            logger.info(f"✅ PDF 统一处理完成，共 {len(raw_blocks)} 个块")

        # 快照
        image_paths = doc_metadata.get("image_paths", [])
        if image_paths:
            captioned = sum(1 for i in image_paths if i.get("caption"))
            logger.info(f"🖼️ 图片: {len(image_paths)} 张，已描述: {captioned} 张")

        try:
            snapshot = PipelineSnapshot()
            snapshot.save(
                stage="after_load",
                source_file=filename,
                blocks=raw_blocks,
                doc_metadata=doc_metadata,
                elapsed=time.time() - start_time,
                ext=ext,
            )
        except Exception as e:
            logger.warning(f"⚠️ 快照写入失败 (after_load): {e}")

        # 确保表存在
        await ensure_all_tables()

        # 4～7. 执行下游流水线：切块 → 向量化 → 入库 → 验证
        await _run_full_pipeline(
            raw_blocks=raw_blocks,
            run_id=run_id,
            file_path=filename,
            kb_id=kb_id,
            doc_metadata=doc_metadata,
            ext=ext,
            start_time=start_time,
        )

        # ── 成功完成 ──
        await store.update_task_status(task_id, "COMPLETED")
        elapsed = time.time() - start_time
        logger.info(f"✅ 任务完成: {filename}, 耗时 {elapsed:.2f}s")

    except Exception as e:
        logger.exception(f"❌ 任务失败: {filename}")
        await store.update_task_status(
            task_id, "FAILED", error_message=str(e)[:500]
        )


# ═══════════════════════════════════════════════════════════════════════
# 内部：切块 → 向量化 → 入库 → 验证
# ═══════════════════════════════════════════════════════════════════════

async def _run_full_pipeline(raw_blocks, run_id, file_path, kb_id, doc_metadata, ext, start_time):
    # ── 资源状态（开始）──
    if _PSUTIL_AVAILABLE:
        proc = psutil.Process()
        mem = proc.memory_info()
        logger.info("📊 资源状态 (开始): RSS=%.1fMB, VMS=%.1fMB, CPU=%d%%",
                     mem.rss / 1024 / 1024, mem.vms / 1024 / 1024, psutil.cpu_percent())

    # 4. 语义切块
    logger.info("✂️ 正在切块...")
    t_chunk_start = time.time()
    chunker = SemanticChunker(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP
    )
    chunks = chunker.chunk(raw_blocks)
    logger.info(f"✂️ 切块完成，共 {len(chunks)} 个文本块 (耗时 {time.time() - t_chunk_start:.2f}s)")
    for idx, c in enumerate(chunks[:10]):
        content_preview = c["content"][:80].replace("\n", "\\n")
        logger.info(f"  [{idx}] {content_preview}...")

    if not chunks:
        logger.warning("⚠️ 切块后为空")
        raise RuntimeError("切块后为空，无法继续入库")

    try:
        snapshot = PipelineSnapshot()
        snapshot.save(
            stage="after_chunk",
            source_file=file_path,
            blocks=chunks,
            doc_metadata=doc_metadata,
            elapsed=time.time() - start_time,
            ext=ext,
        )
    except Exception as e:
        logger.warning(f"⚠️ 快照写入失败 (after_chunk): {e}")

    # 5. 向量化
    logger.info(f"🧬 正在向量化（模型: {settings.EMBEDDING_MODEL}）...")
    t_embed_start = time.time()
    embedder = OllamaEmbedder(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.EMBEDDING_MODEL
    )
    texts = [c["content"] for c in chunks]
    embeddings = await embedder.embed_batch(texts)

    if not embeddings:
        logger.error("❌ 向量化失败")
        raise RuntimeError("向量化失败，所有文本嵌入为空")

    logger.info(f"🧬 向量化完成，共 {len(embeddings)} 条，维度: {len(embeddings[0]) if embeddings else 0} (耗时 {time.time() - t_embed_start:.2f}s)")

    try:
        embed_snapshot_blocks = [
            {"content": chunks[i]["content"], "metadata": chunks[i].get("metadata", {})}
            for i in range(len(chunks))
        ]
        snapshot = PipelineSnapshot()
        snapshot.save(
            stage="after_embed",
            source_file=file_path,
            blocks=embed_snapshot_blocks,
            doc_metadata=doc_metadata,
            elapsed=time.time() - start_time,
            ext=ext,
        )
    except Exception as e:
        logger.warning(f"⚠️ 快照写入失败 (after_embed): {e}")

    # 6. 入库
    logger.info("💾 正在入库 PostgreSQL...")
    t_store_start = time.time()
    # ★ 根据 Embedding 模型选择表后缀 ★
    embedding_model = settings.EMBEDDING_MODEL.lower()
    if "bge-m3" in embedding_model or "bgem3" in embedding_model:
        model_key = "bge-m3"
    else:
        model_key = "nomic-embed"
    
    config = EMBEDDING_CONFIG[model_key]
    logger.info(f"使用模型: {config['ollama_model']}，表: knowledge_embeddings{config['table_suffix']}")
    
    store = VectorStore(table_suffix=config["table_suffix"])
    await store.insert_many(
        chunks=chunks,
        embeddings=embeddings,
        source_file=file_path,
        kb_id=kb_id
    )

    elapsed = time.time() - start_time
    logger.info(f"✅ 入库完成！共 {len(chunks)} 条向量存入 knowledge_embeddings (入库耗时 {time.time() - t_store_start:.2f}s)")
    logger.info(f"⏱️ 总耗时: {elapsed:.2f} 秒")

    # ── 资源状态（结束）──
    if _PSUTIL_AVAILABLE:
        proc = psutil.Process()
        mem = proc.memory_info()
        logger.info("📊 资源状态 (结束): RSS=%.1fMB, VMS=%.1fMB, CPU=%d%%",
                     mem.rss / 1024 / 1024, mem.vms / 1024 / 1024, psutil.cpu_percent())

    # 7. 验证检索
    logger.info("\n🔍 测试检索...")
    test_query = chunks[0]["content"][:50] + "..." if chunks else "测试"
    test_emb = await embedder.embed_text(test_query)
    results = await store.search(test_emb, kb_id=kb_id, top_k=2)
    if results:
        logger.info(f"✅ 检索测试通过，返回 {len(results)} 条结果")
        for i, r in enumerate(results, 1):
            logger.info(f"   {i}. 相似度: {r['similarity']:.4f} | {r['content'][:50]}...")

    audit_logger.info(json.dumps({
        "event": "ingest_complete",
        "run_id": run_id,
        "file": file_path,
        "kb_id": kb_id,
        "chunks": len(chunks),
        "elapsed": elapsed,
        "status": "success"
    }, ensure_ascii=False))
    logger.info(f"=== RUN END: {run_id} ===")


# ═══════════════════════════════════════════════════════════════════════
# CLI 入口（保持向后兼容）
# ═══════════════════════════════════════════════════════════════════════

async def main(file_path: str, kb_id: str = "default", fail_on_caption_error: bool = True):
    """CLI 入口：读取文件后委托 run_ingest_pipeline。"""
    try:
        with open(file_path, "rb") as f:
            content = f.read()
        logger.info(f"✅ 文件读取成功，大小: {len(content)} bytes")
    except FileNotFoundError:
        logger.error(f"❌ 文件不存在: {file_path}")
        return

    task_id = str(uuid.uuid4())
    store = VectorStore()

    await store.create_task(
        task_id=task_id,
        kb_id=kb_id,
        source_file=file_path,
        status="PENDING",
    )

    await run_ingest_pipeline(
        content=content,
        filename=Path(file_path).name,
        kb_id=kb_id,
        task_id=task_id,
        fail_on_caption_error=fail_on_caption_error,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 1 离线文档摄入")
    parser.add_argument("--file", "-f", required=True, help="文档路径")
    parser.add_argument("--kb_id", "-k", default="default", help="知识库ID")
    parser.add_argument(
        "--allow_caption_fallback",
        action="store_true",
        default=False,
        help="允许图片描述失败时降级处理",
    )
    args = parser.parse_args()
    fail_mode = not args.allow_caption_fallback
    asyncio.run(main(args.file, args.kb_id, fail_on_caption_error=fail_mode))

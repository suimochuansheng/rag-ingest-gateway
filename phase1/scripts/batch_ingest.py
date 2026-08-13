"""
批量文档摄入脚本：自动按文件名前缀映射 kb_id

用法:
    python scripts/batch_ingest.py --model nomic-embed
    python scripts/batch_ingest.py --model bge-m3 --dir phase1/data
"""

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

# ── 确保 phase1/src 和 phase1 均可被导入 ──
_PHASE1_DIR = Path(__file__).resolve().parent.parent  # phase1/
_SRC_DIR = _PHASE1_DIR / "src"
for _p in (_PHASE1_DIR, _SRC_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.ingest_knowledge import run_ingest_pipeline

# ── 文件名 → kb_id 映射表 ─────────────────────────────────────
KB_MAP: dict[str, str] = {
    "tech_architecture.md": "tech",
    "product_manual.md": "tech",
    "financial_report.md": "tech",
    "policy_handbook.md": "tech",
    "tech_blog.md": "tech",
    "test_rag_spec.pdf": "tech",
}


def resolve_kb_id(filename: str) -> str:
    """根据文件名查找映射表，未命中时取文件名第一个下划线前段作为 kb_id。"""
    if filename in KB_MAP:
        return KB_MAP[filename]
    return filename.split("_")[0] if "_" in filename else "default"


async def batch_ingest(model_key: str, docs_dir: str):
    if model_key == "bge-m3":
        os.environ["EMBEDDING_MODEL"] = "bge-m3"
    else:
        os.environ["EMBEDDING_MODEL"] = "nomic-embed-text"

    docs_path = Path(docs_dir)
    files = sorted(
        [f for f in docs_path.iterdir() if f.is_file() and not f.name.startswith(".")],
        key=lambda x: x.name,
    )

    print(
        f"🚀 开始批量摄入，共 {len(files)} 个文件"
        f" | 嵌入模型: {os.environ['EMBEDDING_MODEL']}\n"
        + "=" * 60,
    )

    success = 0
    failed = 0

    for idx, file_path in enumerate(files, 1):
        filename = file_path.name
        kb_id = resolve_kb_id(filename)

        print(f"[{idx}/{len(files)}] 正在处理: {filename} ➔ kb_id: {kb_id}")

        with open(file_path, "rb") as f:
            content = f.read()

        # task_id 必须唯一（避免 pipeline_jobs 主键冲突）
        task_id = str(uuid.uuid4())

        try:
            await run_ingest_pipeline(
                content=content,
                filename=filename,
                kb_id=kb_id,
                task_id=task_id,
                fail_on_caption_error=False,
            )
            success += 1
            print(f"✅ 完成: {filename}\n")
        except Exception as e:
            failed += 1
            print(f"❌ 失败: {filename} | 错误: {e}\n")

    print("=" * 60)
    print(f"📊 批量摄入完成: 成功 {success}, 失败 {failed}, 总计 {len(files)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批量文档摄入")
    parser.add_argument(
        "--model", default="nomic-embed", choices=["nomic-embed", "bge-m3"],
        help="Embedding 模型",
    )
    parser.add_argument(
        "--dir", default=str(Path(__file__).parent.parent / "data"),
        help="文档目录（默认 phase1/data）",
    )
    args = parser.parse_args()

    asyncio.run(batch_ingest(args.model, args.dir))

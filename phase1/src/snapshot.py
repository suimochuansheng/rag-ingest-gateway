# /rag_data_dispose/phase1/src/snapshot.py
"""
Pipeline Snapshot — 统一中间产物快照

在 Pipeline 的每个关键阶段（Loader 产出后、Chunking 产出后）拍一张快照，
输出统一的 JSON + 人类可读 TXT，覆盖 PDF / Word / Excel / Markdown 全部格式。

用法:
    from snapshot import PipelineSnapshot

    snapshot = PipelineSnapshot()
    snapshot.save(
        stage="after_load",
        source_file="/path/to/sample.pdf",
        blocks=raw_blocks,
        doc_metadata=doc_metadata,
        elapsed=2.3,
        ext="pdf",
    )
"""

import json
import time
from pathlib import Path
from typing import Any


class PipelineSnapshot:
    """将 Pipeline 任意阶段的统一数据结构写入快照文件"""

    def __init__(self, base_dir: Path | None = None):
        """
        Args:
            base_dir: 快照根目录，默认为 phase1/snapshots/
        """
        if base_dir is None:
            base_dir = Path(__file__).parent.parent / "snapshots"
        self.base_dir = Path(base_dir)

    # ────────────────── 公开方法 ──────────────────

    def save(
        self,
        stage: str,
        source_file: str,
        blocks: list[dict[str, Any]],
        doc_metadata: dict[str, Any] | None = None,
        elapsed: float = 0.0,
        ext: str = "",
    ):
        """
        写入一个阶段的快照（JSON + 可读 TXT）。

        Args:
            stage:       阶段名，决定子目录 (e.g. "after_load", "after_chunk")
            source_file: 原始文档的绝对/相对路径
            blocks:      统一的块列表（Loader 产出 or Chunker 产出）
            doc_metadata:文档级元数据（images, title 等），可为 None
            elapsed:     本阶段耗时（秒）
            ext:         源文件扩展名，不含点号 (e.g. "pdf", "md")
        """
        if doc_metadata is None:
            doc_metadata = {}

        stage_dir = self.base_dir / stage
        stage_dir.mkdir(parents=True, exist_ok=True)

        base_name = Path(source_file).stem
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        json_path = stage_dir / f"{base_name}_{timestamp}.json"

        snapshot = self._build_snapshot(
            stage=stage,
            source_file=source_file,
            blocks=blocks,
            doc_metadata=doc_metadata,
            elapsed=elapsed,
            ext=ext,
        )

        # 1. 结构化 JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

        # 2. 人类可读 TXT
        txt_path = stage_dir / f"{base_name}_{timestamp}.txt"
        self._write_readable(snapshot, txt_path)

        # 3. 摘要日志
        print(f"\n📸 [Snapshot] {stage} → {json_path.name}  ({len(snapshot['blocks'])} blocks)")

    # ────────────────── 内部方法 ──────────────────

    def _build_snapshot(
        self,
        stage: str,
        source_file: str,
        blocks: list[dict[str, Any]],
        doc_metadata: dict[str, Any],
        elapsed: float,
        ext: str,
    ) -> dict[str, Any]:
        """组装完整快照字典"""
        normalized_blocks = self._normalize_blocks(blocks)

        # 基本统计
        total_chars = sum(len(b["text"]) for b in normalized_blocks)
        code_count = sum(1 for b in normalized_blocks if b["metadata"].get("is_code"))
        heading_count = sum(1 for b in normalized_blocks if b["metadata"].get("is_heading"))

        return {
            "pipeline": {
                "version": "1.0",
                "stage": stage,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "elapsed_seconds": round(elapsed, 2),
            },
            "document": {
                "source_file": source_file,
                "format": ext,
                "title": doc_metadata.get("title"),
                "image_count": len(doc_metadata.get("image_paths", [])),
                "block_count": len(normalized_blocks),
            },
            "images": doc_metadata.get("image_paths", []),
            "blocks": normalized_blocks,
            "stats": {
                "total_chars": total_chars,
                "avg_chars_per_block": round(total_chars / len(normalized_blocks), 1) if normalized_blocks else 0,
                "code_blocks": code_count,
                "heading_blocks": heading_count,
            },
        }

    def _normalize_blocks(self, blocks: list[dict]) -> list[dict[str, Any]]:
        """
        将 Loader 或 Chunker 产出的块统一为 {index, page_num, text, metadata}。

        Loader 产出:  {"page_num": int, "text": str, "metadata": dict}
        Chunker 产出: {"content": str, "metadata": dict}  (无 page_num)
        """
        result = []
        for i, b in enumerate(blocks):
            text = b.get("text", b.get("content", ""))
            result.append({
                "index": i,
                "page_num": b.get("page_num", 1),
                "text": text,
                "metadata": b.get("metadata", {}),
            })
        return result

    def _write_readable(self, snapshot: dict, path: Path):
        """
        生成人类可读的纯文本预览，方便 grep / 肉眼扫视排查问题。

        格式:
            Source: /path/to/sample.pdf
            Format: pdf  |  Blocks: 12  |  Images: 0
            ============================================================
            [H] 主营业务收入
            星云科技2025年Q2营收为5200万元...
            [CODE] def calc()...
        """
        doc = snapshot["document"]
        lines = []
        lines.append(f"Source: {doc['source_file']}")
        lines.append(
            f"Format: {doc['format']}  |  Blocks: {doc['block_count']}  |  Images: {doc['image_count']}"
        )
        if doc.get("title"):
            lines.append(f"Title: {doc['title']}")

        stats = snapshot.get("stats", {})
        if stats:
            lines.append(
                f"Chars: {stats.get('total_chars', 0)}  |  "
                f"Avg/block: {stats.get('avg_chars_per_block', 0)}  |  "
                f"Code: {stats.get('code_blocks', 0)}  |  "
                f"Headings: {stats.get('heading_blocks', 0)}"
            )

        lines.append("=" * 60)
        lines.append("")

        for b in snapshot["blocks"]:
            meta = b.get("metadata", {})
            # 前缀标记：帮助快速定位
            prefix = ""
            if meta.get("is_heading"):
                prefix = "[H] "
            elif meta.get("is_code"):
                prefix = "[CODE] "
            elif meta.get("sheet"):
                prefix = f"[Sheet:{meta['sheet']}] "
            elif meta.get("source") == "table":
                prefix = "[Table] "

            text_preview = b["text"][:300]
            lines.append(f"--- Block {b['index']} (page {b['page_num']}, {len(b['text'])} chars) ---")
            lines.append(f"{prefix}{text_preview}")
            if len(b["text"]) > 300:
                lines.append("    ... (truncated)")
            lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

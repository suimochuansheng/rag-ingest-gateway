"""语义切块器：递归切分 + 标题合并 + 表格保护 + 中文噪声清洗。

核心能力：
1. 标题-正文强制合并 — 杜绝孤立标题微型块
2. Markdown 表格保护 — 保留完整表格语法，不切断换行
3. 中文汉字间空格清洗 — "向 量" → "向量"
4. 滑动窗口控量 — chunk_size=512, overlap=100, 过滤 < 50 字符碎片
"""

import logging
import re
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────
MIN_CHUNK_LENGTH = 50
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", " ", ""]

# ── 预编译正则 ───────────────────────────────────────────────
_HEADING_RE = re.compile(r"^#{1,6}\s")
# 提取首行标题：匹配 "# 标题" 或 "## 标题" 直到下一个换行
_HEADING_EXTRACT_RE = re.compile(r"^(#{1,6}\s+.+?)(?:\n\n|\n|$)", re.MULTILINE)
# 表格行识别：行首尾均为 | 
_TABLE_LINE_RE = re.compile(r"^\s*\|.+\|\s*$")
# 中文汉字间无意义空格
_CJK_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")


class SemanticChunker:
    """语义切块器：将 Loader 输出的标准化 Block 列表切分为可控大小的 Chunk。"""

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 100):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=_SEPARATORS,
            length_function=len,
            keep_separator=True,  # 保留分隔符以维持文本结构
        )

    # ── 静态工具方法 ──────────────────────────────────────────

    @staticmethod
    def _clean_chinese_spaces(text: str) -> str:
        """去除中文汉字之间的异常空格（如 "向 量" → "向量"）。"""
        return _CJK_SPACE_RE.sub("", text)

    @staticmethod
    def _is_heading(text: str) -> bool:
        """判定文本是否以 Markdown 标题开头。"""
        return bool(_HEADING_RE.match(text.strip()))

    @staticmethod
    def _is_table(text: str) -> bool:
        """判定文本是否包含完整的 Markdown 表格（至少表头 + 分隔行）。"""
        lines = text.strip().split("\n")
        pipe_lines = [l for l in lines if _TABLE_LINE_RE.match(l)]
        return len(pipe_lines) >= 2

    @staticmethod
    def _extract_first_heading(text: str) -> tuple[str, str]:
        """提取首行标题。返回 (heading, remainder_body)。

        示例:
            "## 简介\\n\\n正文内容" → ("## 简介", "正文内容")
            "普通段落文本"         → ("", "普通段落文本")
        """
        m = _HEADING_EXTRACT_RE.match(text)
        if m:
            heading = m.group(1).strip()
            remainder = text[m.end():].strip()
            return heading, remainder
        return "", text

    # ── 核心：Block 级预合并 ──────────────────────────────────

    def _merge_heading_blocks(
        self, blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """将标题 Block 与紧随其后的正文 Block 合并，杜绝孤立标题块。

        遍历所有输入 block，当遇到标题 block 时，将其与后续的非标题 block
        合并为一个大块。合并后 text 包含标题行 + 后续正文的全部内容。
        """
        if not blocks:
            return []

        merged: list[dict[str, Any]] = []
        i = 0

        while i < len(blocks):
            text = blocks[i].get("text", "").strip()
            meta = blocks[i].get("metadata", {})

            if self._is_heading(text) and i + 1 < len(blocks):
                # 收集此标题后的所有非标题 body block，直到遇到下一个标题
                parts = [text]
                j = i + 1
                while j < len(blocks):
                    next_text = blocks[j].get("text", "").strip()
                    if self._is_heading(next_text):
                        break  # 遇到新标题，停止收集
                    if next_text:
                        parts.append(next_text)
                    j += 1

                if len(parts) > 1:
                    # 有正文跟随 → 合并
                    merged_text = "\n\n".join(parts)
                    merged.append({
                        "text": merged_text,
                        "metadata": meta,
                    })
                    i = j
                else:
                    # 孤立标题（没有正文跟随）→ 保留原样，后续会因 < 50 被过滤
                    merged.append({"text": text, "metadata": meta})
                    i += 1
            else:
                merged.append({"text": text, "metadata": meta})
                i += 1

        return merged

    # ── 核心：单 Block 切分（含标题前缀注入 + 表格保护）───────

    def _split_block(
        self, text: str, meta: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """将单个（已合并的）Block 文本切分为最终 Chunk 列表。"""
        chunks: list[dict[str, Any]] = []

        # ── 规则 1：表格 → 整体保留，不切分 ──
        if self._is_table(text):
            cleaned = text.strip()
            if len(cleaned) >= MIN_CHUNK_LENGTH:
                chunks.append({"content": cleaned, "metadata": meta})
            return chunks

        # ── 规则 2：标题 + 正文合并 → 标题作为每个子块的前缀 ──
        heading, body = self._extract_first_heading(text)

        if heading and body:
            # 正文部分用 splitter 切分为子块
            body_parts = self.splitter.split_text(body)
            for part in body_parts:
                part = part.strip()
                if not part:
                    continue
                combined = heading + "\n" + part
                if len(combined) >= MIN_CHUNK_LENGTH:
                    chunks.append({"content": combined, "metadata": meta})
        elif heading and not body:
            # 孤立标题（预合并未覆盖的边缘情况）→ 丢弃
            logger.debug("丢弃孤立标题块 (len=%d): %s", len(heading), heading[:60])
        else:
            # ── 规则 3：普通文本 → 标准递归切分 ──
            raw_parts = self.splitter.split_text(text)
            for part in raw_parts:
                part = part.strip()
                if not part:
                    continue
                if len(part) >= MIN_CHUNK_LENGTH:
                    chunks.append({"content": part, "metadata": meta})

        return chunks

    # ── 公开入口 ──────────────────────────────────────────────

    def chunk(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将标准化块切分为语义完整的 Chunk。

        Args:
            blocks: Loader 输出的 Block 列表，每个 Block 含 "text" 和 "metadata"。

        Returns:
            Chunk 列表，每个 Chunk 含 "content" 和 "metadata"。
            长度 < MIN_CHUNK_LENGTH 的碎片被丢弃。
            标题会被注入到其所有子块的文本前缀中。
        """
        # ══ Step 1: 中文噪声清洗 ══
        for b in blocks:
            if "text" in b:
                b["text"] = self._clean_chinese_spaces(b["text"])

        # ══ Step 2: Block 级标题-正文预合并 ══
        merged_blocks = self._merge_heading_blocks(blocks)

        # ══ Step 3: 逐块切分 ══
        final_chunks: list[dict[str, Any]] = []
        for block in merged_blocks:
            text = block.get("text", "")
            if not text or not text.strip():
                continue
            meta = block.get("metadata", {})
            final_chunks.extend(self._split_block(text, meta))

        # ══ Step 4: 诊断日志 ══
        if final_chunks:
            lengths = [len(c["content"]) for c in final_chunks]
            logger.info(
                "切块完成: 共 %d 个, 长度范围 [%d, %d], 平均 %.0f 字符",
                len(final_chunks), min(lengths), max(lengths),
                sum(lengths) / len(lengths),
            )
            # 打印前 5 个 Chunk 预览，方便确认无孤立标题
            preview_n = min(5, len(final_chunks))
            for i in range(preview_n):
                preview = final_chunks[i]["content"][:100].replace("\n", "\\n")
                has_heading = "📌" if self._is_heading(final_chunks[i]["content"]) else "  "
                logger.info(
                    "  %s[%d] %d 字符: %s...",
                    has_heading, i, lengths[i], preview,
                )
        else:
            logger.warning("切块结果为空，请检查输入文本")

        return final_chunks

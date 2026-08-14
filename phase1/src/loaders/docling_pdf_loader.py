#!/usr/bin/env python
"""
DOCLING PDF LOADER — IR 架构核心入口
====================================

【文件定位】
    本文档实现了 IR（中间表示）架构中的「PDF → Markdown」转换层。
    它位于整个数据摄入链路的最前端，负责将各种复杂的 PDF 文档
    （含表格、双栏、页眉页脚、扫描件）转化为干净的 Markdown 文本，
    使下游所有处理环节（清洗 → 切块 → 向量化 → 入库）无需区分
    文档来源，实现统一处理。

【在 IR 架构中的位置】
    ┌─────────────────────────────────────────────────────────────┐
    │                   文档摄入统一入口                           │
    │              POST /ingest (FastAPI)                        │
    └─────────────────────────┬───────────────────────────────────┘
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │                   格式路由 (扩展名判断)                      │
    │  .md   → MarkdownLoader (原生)                             │
    │  .pdf  → DoclingPDFLoader (本模块)  ← 你在这里             │
    │  .docx → DoclingDocxLoader (待实现)                        │
    │  .xlsx → ExcelRowLoader (待实现)                           │
    └─────────────────────────┬───────────────────────────────────┘
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │                统一中间表示 (IR)                            │
    │  所有 Loader 输出标准化 Block 列表:                         │
    │  [{"page_num": 1, "text": "...", "metadata": {...}}]       │
    └─────────────────────────┬───────────────────────────────────┘
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │               下游统一管道 (不变)                           │
    │  MarkdownCleaner → SemanticChunker → OllamaEmbedder       │
    │  → VectorStore                                             │
    └─────────────────────────────────────────────────────────────┘

【核心能力】
    1. 基于 Docling 引擎解析 PDF，输出结构化 AST
    2. 过滤页眉页脚（page-header / page-footer）噪声
    3. 导出纯净 Markdown 文本（保留图片占位符 ![](image.png)）
    4. 调试输出到 debug_cleaned/ 便于人工检查
    5. 所有同步操作通过 asyncio.to_thread 隔离，不阻塞事件循环

【设计决策与原理】
    ┌────────────────┬────────────────────────────────────────────┐
    │ 决策点         │ 原因                                       │
    ├────────────────┼────────────────────────────────────────────┤
    │ 使用 Docling   │ 比 pdfplumber 更强大，内置：              │
    │ 而非 pdfplumber│ - 双栏自动重排（无需手动算法）             │
    │                │ - TableFormer 深度学习表格识别            │
    │                │ - AST 节点过滤（页眉页脚精准移除）         │
    │                │ - 原生 Markdown 导出（无需手动拼接）      │
    ├────────────────┼────────────────────────────────────────────┤
    │ 输出为 Markdown│ 与原生 .md 文件格式统一，下游全部复用：   │
    │ 而非自定义格式 │ MarkdownCleaner → Chunker 零改动支持 PDF │
    ├────────────────┼────────────────────────────────────────────┤
    │ 不处理图片语义 │ 图片占位符保留，由下游 ImageCaptioner      │
    │ 描述           │ 统一处理（避免重复实现）                   │
    ├────────────────┼────────────────────────────────────────────┤
    │ OCR 可配置关闭 │ OCR 依赖系统级 tesseract + poppler，       │
    │                │ 默认关闭避免开发环境报错                   │
    ├────────────────┼────────────────────────────────────────────┤
    │ 调试输出       │ 便于人工验证 Docling 导出质量，            │
    │ debug_cleaned/ │ 出现解析问题时快速定位是 Docling 问题      │
    │                │ 还是下游清洗问题                           │
    └────────────────┴────────────────────────────────────────────┘

【上下游依赖】
    上游：ingest_knowledge.py 通过 loader_map["pdf"] 调用
    下游：MarkdownCleaner（复用现有 4 层清洗管道）
    外部：Docling 引擎（pip install docling）

【扩展指南】
    如需支持新的 PDF 特性（如密码保护、特定字体），修改：
        1. _parse_pdf_sync() 中的 pipeline_options 配置
        2. 或在 Docling 文档中查找对应参数
    如需调整页眉页脚过滤逻辑，修改：
        export_to_markdown(included_content_layers={ContentLayer.BODY}) 参数

【维护者须知】
    - 本模块的 load() 是 async def，但内部用 asyncio.to_thread
      包装了同步的 _parse_pdf_sync()，这是为了让 PDF 解析不阻塞
      FastAPI 主事件循环。
    - 如果未来 Docling API 升级，重点关注 export_to_markdown()
      的参数变化和 doc.meta 的访问路径。
    - 调试输出默认关闭，如需开启设置 DoclingPDFLoader(debug=True)。
"""
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DOCLING PDF LOADER — IR 架构核心入口
====================================
"""
import asyncio
import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx

# 导入 Docling 2.x 标准的输入格式枚举与格式包装器
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.transforms.serializer.markdown import ImageRefMode
from docling_core.types.doc.document import ContentLayer

from .base import DocumentLoader

# ── 可选依赖：AcceleratorOptions（Docling >=2.x）+ torch（GPU 检测）──
try:
    from docling.datamodel.pipeline_options import AcceleratorOptions
    _HAS_ACCELERATOR = True
except ImportError:
    AcceleratorOptions = None  # type: ignore
    _HAS_ACCELERATOR = False

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    torch = None  # type: ignore
    _HAS_TORCH = False

logger = logging.getLogger(__name__)


class DoclingPDFLoader(DocumentLoader):
    def __init__(
        self,
        debug_dir: str | None = None,
        enable_ocr: bool = False,
        ocr_lang: str = "chi_sim+eng",
        enable_table_structure: bool = True,
    ):
        self.debug_dir = debug_dir or str(Path(__file__).parent.parent.parent / "debug_cleaned")
        # ★ 强制禁用 OCR（避免耗时）★
        self.enable_ocr = False  # 强制关闭
        self.ocr_lang = ocr_lang
        self.enable_table_structure = enable_table_structure
        os.makedirs(self.debug_dir, exist_ok=True)

    def _html_table_to_markdown(self, html_table: str) -> str:
        """
        将 Docling 导出的 HTML <table> 标签转换为标准 Markdown Table。
        
        输入: <table>存储引擎(Engine)写入吞吐量(QPS)...</table>
        输出: | 存储引擎(Engine) | 写入吞吐量(QPS) | ... |
            | --- | --- | --- |
            | PostgreSQL + pgvector | 1,200 | ... |
        """
        # 提取 <tbody> 或直接提取 <tr> 内容
        # 使用正则提取所有行
        rows = re.findall(r'<tr>(.*?)</tr>', html_table, re.DOTALL)
        if not rows:
            return html_table  # 没找到表格，原样返回

        table_rows = []
        for row in rows:
            # 提取所有 <td> 或 <th>
            cells = re.findall(r'<t[dh]>(.*?)</t[dh]>', row, re.DOTALL)
            if cells:
                # 清理单元格内的 HTML 标签和多余空白
                cleaned_cells = []
                for cell in cells:
                    # 移除 <p>, <b>, <i> 等标签，保留文本
                    cell_text = re.sub(r'<[^>]+>', '', cell).strip()
                    cleaned_cells.append(cell_text)
                table_rows.append(cleaned_cells)

        if not table_rows:
            return html_table

        # 构建 Markdown Table
        col_count = len(table_rows[0])
        md_lines = []

        # 表头（第一行）
        md_lines.append("| " + " | ".join(table_rows[0]) + " |")
        # 分隔线
        md_lines.append("| " + " | ".join(["---"] * col_count) + " |")
        # 数据行
        for row in table_rows[1:]:
            # 补齐列数
            while len(row) < col_count:
                row.append("")
            md_lines.append("| " + " | ".join(row) + " |")

        return "\n".join(md_lines)

    def _parse_pdf_sync(self, content: bytes) -> tuple[str, dict[str, Any], str]:
        """CPU-bound: 使用 Docling 解析 PDF 并导出 Markdown（在 to_thread 中运行）。

        Returns:
            (full_markdown, doc_metadata, tmp_path)
        """
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
            tmp_file.write(content)
            tmp_path = tmp_file.name

        # ★ 配置 Pipeline（性能优化版，低版本兼容）★
        pipeline_options = PdfPipelineOptions()
        try:
            pipeline_options.pdf_backend = "pypdfium2"
        except (AttributeError, TypeError, ValueError):
            logger.debug("当前 Docling 版本不支持 pdf_backend，使用默认后端")

        if _HAS_ACCELERATOR:
            device = "cuda" if (_HAS_TORCH and torch.cuda.is_available()) else "cpu"
            pipeline_options.accelerator_options = AcceleratorOptions(
                num_threads=4, device=device,
            )
        else:
            logger.debug("AcceleratorOptions 不可用，跳过 GPU/多线程加速设置")
        # ── 关闭 OCR ──
        pipeline_options.do_ocr = False
        if hasattr(pipeline_options, 'ocr_engine'):
            pipeline_options.ocr_engine = None
        # ── 表格识别 ──
        pipeline_options.do_table_structure = self.enable_table_structure
        # ── 图片提取：保留独立图片（供 ImageCaptioner），关闭全页截图 ──
        pipeline_options.images_scale = 1.0   # 降低分辨率加速
        pipeline_options.generate_page_images = False    # 关闭全页截图耗时操作
        pipeline_options.generate_picture_images = True  # 提取独立图片供下游 ImageCaptioner

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=pipeline_options
                )
            }
        )

        result = converter.convert(tmp_path)
        doc = result.document

        # 提取元数据（2.x 兼容）
        doc_metadata: dict[str, Any] = {
            "title": "Unknown",
            "author": "Unknown",
            "total_pages": 0,
            "source": "docling",
        }
        try:
            if hasattr(doc, "pages") and doc.pages:
                doc_metadata["total_pages"] = len(doc.pages)
            elif hasattr(doc, "page_count"):
                doc_metadata["total_pages"] = doc.page_count

            if hasattr(doc, "meta") and doc.meta:
                if isinstance(doc.meta, dict):
                    doc_metadata["title"] = doc.meta.get("title", "Unknown")
                    doc_metadata["author"] = doc.meta.get("author", "Unknown")
                else:
                    doc_metadata["title"] = getattr(doc.meta, "title", "Unknown") or "Unknown"
                    doc_metadata["author"] = getattr(doc.meta, "author", "Unknown") or "Unknown"
        except Exception:
            pass

        # ★ 解析详情日志：记录文档规模便于性能对比
        picture_count = 0
        try:
            if hasattr(doc, "pages") and doc.pages:
                for p in doc.pages:
                    if hasattr(p, "pictures") and p.pictures:
                        picture_count += len(p.pictures)
        except Exception:
            pass
        logger.info(
            "📄 Docling 解析完成: 总页数=%d, 标题=%s, 提取图片=%d",
            doc_metadata["total_pages"], doc_metadata["title"], picture_count,
        )

        # ★ 导出 Markdown：先保存图片到磁盘，再用 REFERENCED 模式引用 ★
        artifacts_dir = Path(self.debug_dir) / "docling_images"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        new_doc = doc._make_copy_with_refmode(
            artifacts_dir=artifacts_dir,
            image_mode=ImageRefMode.REFERENCED,
            page_no=None,
            reference_path=None,
            include_page_images=False,
        )

        full_markdown = new_doc.export_to_markdown(
            image_mode=ImageRefMode.REFERENCED,
            included_content_layers={ContentLayer.BODY},
        )

        return full_markdown, doc_metadata, tmp_path

    async def _download_web_images(
        self, full_markdown: str, doc_metadata: dict[str, Any],
    ) -> str:
        """I/O-bound: 异步下载 Markdown 中的网络图片到本地。

        Returns:
            替换完 URL 后的 markdown 文本。
        """
        url_pattern = re.compile(r'!\[(.*?)\]\((https?://[^\)]+)\)', re.DOTALL)
        url_matches = url_pattern.findall(full_markdown)
        if not url_matches:
            return full_markdown

        logger.info(f"🌐 发现 {len(url_matches)} 张网络图片，正在下载...")
        web_images_dir = Path(self.debug_dir) / "web_images"
        web_images_dir.mkdir(parents=True, exist_ok=True)

        downloaded_cache: dict[str, str] = {}

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for alt, url in url_matches:
                if url.startswith('file://') or url.startswith('/') or url.startswith('.'):
                    continue
                if url in downloaded_cache:
                    continue

                url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
                ext_match = re.search(r'\.(png|jpg|jpeg|gif|webp|bmp)(\?|$)', url, re.IGNORECASE)
                ext = ext_match.group(1) if ext_match else 'png'
                local_filename = f"web_{url_hash}.{ext}"
                local_path = web_images_dir / local_filename

                if local_path.exists():
                    downloaded_cache[url] = str(local_path)
                    continue

                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    local_path.write_bytes(response.content)
                    logger.info(f"✅ 下载成功: {local_filename}")
                    downloaded_cache[url] = str(local_path)
                except Exception as e:
                    logger.warning(f"⚠️ 网络图片下载失败: {url} - {e}")
                    downloaded_cache[url] = url

        # 替换 Markdown 中的 URL
        for alt, url in url_matches:
            if url.startswith('file://') or url.startswith('/') or url.startswith('.'):
                continue
            local = downloaded_cache.get(url)
            if local and local != url:
                full_markdown = full_markdown.replace(
                    f'![{alt}]({url})', f'![{alt}]({local})'
                )

        doc_metadata["web_images_downloaded"] = len(downloaded_cache)
        doc_metadata["web_images_dir"] = str(web_images_dir)
        return full_markdown

    async def load(self, content: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """异步入口：PDF 解析 (to_thread) → 网络图片下载 (await) → 后处理。

        1. CPU-bound Docling 解析 → asyncio.to_thread
        2. I/O-bound 网络下载 → await httpx.AsyncClient
        3. HTML table 转换 + 调试输出 → 同步（轻量，不阻塞）
        """
        # Step 1: CPU-bound PDF 解析（线程池隔离 + 超时保护）
        try:
            async with asyncio.timeout(300):  # 5 分钟超时，防止大文件卡死
                full_markdown, doc_metadata, tmp_path = await asyncio.to_thread(
                    self._parse_pdf_sync, content,
                )
        except TimeoutError:
            logger.error("❌ Docling 解析超时 (>300s)，文件可能过大或损坏")
            raise RuntimeError("PDF 解析超时（>300 秒），文件可能过大或损坏")

        try:
            # Step 2: I/O-bound 网络下载（async/await 真·非阻塞）
            full_markdown = await self._download_web_images(full_markdown, doc_metadata)

            # Step 3: 轻量后处理
            full_markdown = re.sub(
                r'<table>.*?</table>',
                lambda m: self._html_table_to_markdown(m.group(0)),
                full_markdown,
                flags=re.DOTALL,
            )

            # 调试输出
            content_hash = hashlib.md5(content[:4096]).hexdigest()[:8]
            debug_path = Path(self.debug_dir) / f"pdf_clean_{content_hash}.md"
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write("# Docling 导出 - 图片已保存至 images/，页眉页脚已过滤\n\n")
                f.write(full_markdown)
            logger.info(f"📝 调试输出已写入: {debug_path}")

            blocks = [{
                "page_num": 1,
                "text": full_markdown,
                "metadata": {
                    "source": "docling",
                    "total_pages": doc_metadata["total_pages"],
                    "title": doc_metadata["title"],
                }
            }]
            return blocks, doc_metadata

        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

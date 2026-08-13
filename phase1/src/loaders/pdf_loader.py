import os
import logging
import re
import asyncio
from typing import List, Dict, Any, Tuple, Optional
from io import BytesIO

import pdfplumber
from pdfplumber.page import Page

# OCR 依赖（安全导入）
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    HAS_OCR_LIBS = True
except ImportError:
    HAS_OCR_LIBS = False

from .base import DocumentLoader

logger = logging.getLogger(__name__)


class PDFLoader(DocumentLoader):
    """
    生产级 PDF 解析基础模块（已固化清洗与防假表版本）
    
    设计边界说明：
    1. 限制表格提取策略为 "lines"，彻底阻断多列布局被误判为“假表格”的问题。
    2. 内置清洗与标题注入过滤器（_clean_and_format_text），自动还原 Markdown 标题级标志（## ），
       以配合下游进行高精度内容标题切块。
    3. 异步线程池包装同步阻塞操作。
    """

    def __init__(
        self,
        enable_ocr: bool = True,
        ocr_lang: str = "chi_sim+eng",
        ocr_chars_threshold: int = 15,
        ocr_image_ratio_threshold: float = 0.3,
        column_gap_ratio: float = 0.1,
        min_words_per_column: int = 5,
        y_tolerance: float = 3.0,
    ):
        super().__init__()
        self.enable_ocr = enable_ocr
        self.ocr_lang = ocr_lang
        self.ocr_chars_threshold = ocr_chars_threshold
        self.ocr_image_ratio_threshold = ocr_image_ratio_threshold
        self.column_gap_ratio = column_gap_ratio
        self.min_words_per_column = min_words_per_column
        self.y_tolerance = y_tolerance

        if self.enable_ocr and not HAS_OCR_LIBS:
            logger.warning(
                "⚠️ 未检测到 pdf2image 或 pytesseract，OCR 降级功能已失效。"
                "如需使用，请安装: pip install pdf2image pytesseract，并确保系统有 tesseract 和 poppler。"
            )

    async def load(self, content: bytes) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """加载并解析 PDF，返回标准化块列表和文档元数据"""
        def _sync_parse():
            blocks = []
            doc_metadata = {}
            try:
                with pdfplumber.open(BytesIO(content)) as pdf:
                    doc_metadata = {
                        "title": pdf.metadata.get("Title", "Unknown"),
                        "author": pdf.metadata.get("Author", "Unknown"),
                        "creator": pdf.metadata.get("Creator", "Unknown"),
                        "producer": pdf.metadata.get("Producer", "Unknown"),
                        "total_pages": len(pdf.pages),
                    }

                    # 配置严格的表格识别参数：只认显式框线，杜绝“空文本格”假表
                    table_settings = {
                        "vertical_strategy": "lines",
                        "horizontal_strategy": "lines",
                        "snap_tolerance": 3,
                        "join_tolerance": 3,
                    }

                    for page_num, page in enumerate(pdf.pages, start=1):
                        tables = page.find_tables(table_settings)
                        tables_count = len(tables)

                        # 1. 混合流排序提取
                        page_text = self._extract_mixed_flow(page, tables)

                        # 2. 文本规范化清洗与 Markdown 标题启发式注入
                        page_text = self._clean_and_format_text(page_text)

                        # 3. 智能扫描件判定
                        stripped = page_text.strip()
                        is_scanned = False
                        
                        if len(stripped) < self.ocr_chars_threshold:
                            image_ratio = self._compute_image_area_ratio(page)
                            has_physical_images = len(page.images) > 0
                            
                            if (image_ratio >= self.ocr_image_ratio_threshold) or (len(stripped) == 0 and has_physical_images):
                                is_scanned = True
                                if self.enable_ocr and HAS_OCR_LIBS:
                                    logger.info(f"📄 第 {page_num} 页触发 OCR... (文字字数: {len(stripped)}, 图像占比: {image_ratio:.2f})")
                                    page_text = self._run_ocr_for_page(content, page_num)
                                else:
                                    page_text = stripped or f"[第 {page_num} 页：扫描件，但 OCR 未启用或不可用]"

                        blocks.append({
                            "page_num": page_num,
                            "text": page_text.strip(),
                            "metadata": {
                                "tables_count": tables_count,
                                "is_scanned": is_scanned,
                                "has_images": len(page.images) > 0,
                            }
                        })

                    logger.info(f"✅ PDF 解析完成，共 {len(blocks)} 页，含表格页数：{sum(1 for b in blocks if b['metadata']['tables_count'] > 0)}")    
            except Exception as e:
                logger.exception("❌ PDF 解析失败")
                raise RuntimeError(f"PDF 加载失败: {e}") from e

            return blocks, doc_metadata
        return await asyncio.to_thread(_sync_parse)

    # ------------------------------------------------------------------
    # 核心算法：混合流提取（保持表格物理顺序）
    # ------------------------------------------------------------------
    def _extract_mixed_flow(self, page: Page, tables: List) -> str:
        page_height = page.height
        page_width = page.width
        last_y = 0
        segments = []

        all_words = page.extract_words()
        sorted_tables = sorted(tables, key=lambda t: t.bbox[1])

        for table in sorted_tables:
            tb_top = table.bbox[1]
            tb_bottom = table.bbox[3]

            if tb_top > last_y + 1:
                crop_box = (0, last_y, page_width, tb_top)
                text = self._extract_text_from_region(page, crop_box, all_words)
                if text.strip():
                    segments.append(text.strip())

            table_data = table.extract()
            if table_data:
                md_table = self._table_to_markdown(table_data)
                segments.append(md_table)

            last_y = tb_bottom

        if last_y < page_height - 1:
            crop_box = (0, last_y, page_width, page_height)
            text = self._extract_text_from_region(page, crop_box, all_words)
            if text.strip():
                segments.append(text.strip())

        return "\n\n".join(segments)

    # ------------------------------------------------------------------
    # 文本清洗、垃圾过滤与标题注入算法
    # ------------------------------------------------------------------
    def _clean_and_format_text(self, text: str) -> str:
        """
        清洗 PDF 提取中的无用多余空行，并启发式地将文字标题行格式化为标准的 Markdown Header (## )。
        作用：为下游 Chunker 创造按“物理标题”切块的语义基准。
        """
        if not text:
            return ""

        lines = text.split("\n")
        cleaned_lines = []

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue

            # 1. 标题启发式注入正则
            # 匹配特征 A: "1. 基础单栏文本测试" 或 "2. 物理双栏..."
            # 匹配特征 B: "一、", "二、" 等中文大纲
            # 匹配特征 C: "[左栏：向量检索与 Embedding 维度]" 这种专有标识栏
            is_header_pattern = re.match(
                r"^(\d+[\.\s、]+|[\u4e00-\u9fa5一二三四五六七八九十]+[、\.\s]+|\[(左栏|右栏|正文|OCR).*\])", 
                line_stripped
            )

            if is_header_pattern:
                # 排除 Markdown 表格行本身以防止误伤
                if not line_stripped.startswith("|"):
                    line_stripped = f"\n\n## {line_stripped}\n"

            # 2. 清洗单字/单符号组成的噪声行（除数字外）
            if len(line_stripped) == 1 and not re.match(r"^[0-9a-zA-Z\u4e00-\u9fa5]$", line_stripped):
                continue

            cleaned_lines.append(line_stripped)

        # 重新拼接，限制连续空行最多为 2 个
        result = "\n".join(cleaned_lines)
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result

    # ------------------------------------------------------------------
    # 列感知与水平行聚类文本提取
    # ------------------------------------------------------------------
    def _extract_text_from_region(self, page: Page, bbox: Tuple[float, float, float, float], all_words: List[Dict[str, Any]]) -> str:
        try:
            if not all_words:
                return ""

            x0, y0, x1, y1 = bbox
            filtered = []
            for w in all_words:
                cx = (w['x0'] + w['x1']) / 2
                cy = (w['y0'] + w['y1']) / 2
                if x0 <= cx <= x1 and y0 <= cy <= y1:
                    filtered.append(w)

            if not filtered:
                return ""

            filtered.sort(key=lambda w: w['x0'])
            
            gaps = []
            for i in range(1, len(filtered)):
                gap = filtered[i]['x0'] - filtered[i-1]['x1']
                gaps.append((gap, i))
                
            if not gaps:
                return self._group_words_into_lines(filtered)

            max_gap, split_idx = max(gaps, key=lambda x: x[0])
            page_width = page.width
            
            if max_gap > self.column_gap_ratio * page_width:
                left_words = filtered[:split_idx]
                right_words = filtered[split_idx:]
                
                if len(left_words) >= self.min_words_per_column and len(right_words) >= self.min_words_per_column:
                    left_text = self._group_words_into_lines(left_words)
                    right_text = self._group_words_into_lines(right_words)
                    return left_text + "\n\n" + right_text

            return self._group_words_into_lines(filtered)

        except Exception as e:
            logger.debug(f"列感知行聚类提取失败，退回普通裁切: {e}")
            crop = page.crop(bbox)
            return crop.extract_text() or ""

    def _group_words_into_lines(self, words: List[Dict[str, Any]]) -> str:
        if not words:
            return ""

        words_sorted_vertical = sorted(words, key=lambda w: w['top'])

        lines = []
        current_line = []
        current_top = words_sorted_vertical[0]['top']

        for w in words_sorted_vertical:
            if abs(w['top'] - current_top) <= self.y_tolerance:
                current_line.append(w)
            else:
                current_line.sort(key=lambda w: w['x0'])
                lines.append(" ".join([item['text'] for item in current_line]))
                
                current_line = [w]
                current_top = w['top']

        if current_line:
            current_line.sort(key=lambda w: w['x0'])
            lines.append(" ".join([item['text'] for item in current_line]))

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 表格 → Markdown
    # ------------------------------------------------------------------
    def _table_to_markdown(self, table: List[List[Any]]) -> str:
        if not table or not table[0]:
            return ""

        cleaned = []
        for row in table:
            cleaned_row = []
            for cell in row:
                if cell is None:
                    cleaned_row.append("")
                else:
                    cell_str = str(cell).strip().replace("\n", " ")
                    cleaned_row.append(cell_str)
            cleaned.append(cleaned_row)

        cols = len(cleaned[0])
        for row in cleaned[1:]:
            if len(row) < cols:
                row.extend([""] * (cols - len(row)))
            elif len(row) > cols:
                row = row[:cols]

        lines = []
        lines.append("| " + " | ".join(cleaned[0]) + " |")
        lines.append("| " + " | ".join(["---"] * cols) + " |")
        for row in cleaned[1:]:
            lines.append("| " + " | ".join(row) + " |")

        return "\n" + "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # 图像面积占比
    # ------------------------------------------------------------------
    def _compute_image_area_ratio(self, page: Page) -> float:
        if not page.images:
            return 0.0
        page_area = page.width * page.height
        if page_area == 0:
            return 0.0
        
        total_img_area = 0.0
        for img in page.images:
            x0 = img.get('x0', img.get('x', 0))
            y0 = img.get('top', img.get('y0', 0))
            x1 = img.get('x1', x0 + img.get('width', 0))
            y1 = img.get('bottom', y0 + img.get('height', 0))
            
            w = max(0.0, x1 - x0)
            h = max(0.0, y1 - y0)
            total_img_area += w * h
            
        ratio = total_img_area / page_area
        return min(1.0, ratio)

    # ------------------------------------------------------------------
    # OCR 单页识别
    # ------------------------------------------------------------------
    def _run_ocr_for_page(self, content: bytes, page_num: int) -> str:
        try:
            images = convert_from_bytes(
                content,
                first_page=page_num,
                last_page=page_num,
                fmt='jpeg',
                dpi=150,
            )
            if not images:
                return f"[第 {page_num} 页：无法渲染为图像]"

            ocr_text = pytesseract.image_to_string(images[0], lang=self.ocr_lang)
            if ocr_text.strip():
                return f"[OCR 扫描结果 - 第 {page_num} 页]\n{ocr_text.strip()}"
            else:
                return f"[第 {page_num} 页：OCR 未识别到文本]"
        except Exception as e:
            logger.warning(f"第 {page_num} 页 OCR 失败: {e}")
            return f"[第 {page_num} 页：OCR 执行出错]"
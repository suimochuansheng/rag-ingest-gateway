import asyncio
import logging
import re
from pathlib import Path
from typing import List, Dict, Any
from .base import DocumentLoader
from cleaners.markdown_cleaner import MarkdownCleaner 
from tools.image_captioner import ImageCaptioner

logger = logging.getLogger(__name__)

# ── 预编译正则 ───────────────────────────────────────────────
# 中文汉字间无意义空格清洗（"向 量" → "向量"）
_CJK_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
# 表格行识别：行首尾均为 |
_TABLE_LINE_RE = re.compile(r"^\s*\|.+\|\s*$")


class MarkdownLoader(DocumentLoader):
    def __init__(self, enable_caption: bool = True, fail_on_caption_error: bool = True, base_dir: str = "", max_concurrent: int = 4):
        """
        Args:
            enable_caption: 是否启用图片描述
            fail_on_caption_error: 图片描述失败时是否中断流程
            base_dir: 文档所在目录，用于将相对图片路径解析为绝对路径
            max_concurrent: 图片描述并发上限（防止 API 限流）
        """
        self.cleaner = MarkdownCleaner()
        self.captioner = ImageCaptioner(fail_on_error=fail_on_caption_error) if enable_caption else None
        self.enable_caption = enable_caption
        self.fail_on_caption_error = fail_on_caption_error
        self.base_dir = base_dir
        self.max_concurrent = max_concurrent

    @staticmethod
    def _is_table(text: str) -> bool:
        """判定段落是否包含完整的 Markdown 表格。"""
        lines = text.strip().split("\n")
        pipe_lines = [l for l in lines if _TABLE_LINE_RE.match(l)]
        return len(pipe_lines) >= 2

    @staticmethod
    def _clean_chinese_spaces(text: str) -> str:
        """去除中文汉字之间异常的空格（"向 量" → "向量"）。"""
        return _CJK_SPACE_RE.sub("", text)
    
    async def load(self, content: bytes) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        解析 Markdown 文件，返回 (块列表, 文档级元数据)
        """
        text = content.decode("utf-8", errors="ignore")
        cleaned_text, clean_metadata = await self.cleaner.clean(text)

        # ★ 提取文档标题：取第一个一级标题 (# ) 作为文档级标题
        title_match = re.search(r"^# (.+)$", cleaned_text, re.MULTILINE)
        doc_title = title_match.group(1).strip() if title_match else None
        if doc_title:
            clean_metadata["title"] = doc_title

        # ★ 如果有图片，生成描述（并发）★
        image_paths = clean_metadata.get("image_paths", [])
        if self.enable_caption and image_paths:
            print(f"🖼️ 发现 {len(image_paths)} 张图片，正在生成描述（并发={self.max_concurrent}）...")
            logger.info(
                "🖼️ 图片描述并发数: %d, 共 %d 张图片",
                self.max_concurrent, len(image_paths),
            )
            semaphore = asyncio.Semaphore(self.max_concurrent)

            async def _describe_one(img: dict) -> tuple[dict, str]:
                """并发安全的单张图片描述任务。"""
                async with semaphore:
                    try:
                        img_url = img["url"]
                        if self.base_dir and not img_url.startswith(("http://", "https://", "data:")):
                            img_url = str((Path(self.base_dir) / img_url).resolve())
                        caption = await self.captioner.describe_image(img_url, img["alt"])
                        print(f"   ✅ [{img['index']}] {img['alt']} -> {caption[:30]}...")
                        return img, caption
                    except Exception as e:
                        print(f"   ⚠️ [{img['index']}] 描述生成失败: {e}")
                        return img, f"[图片: {img['alt']}]"

            tasks = [_describe_one(img) for img in image_paths]
            results = await asyncio.gather(*tasks)
            for img, caption in results:
                img["caption"] = caption

            # ★ 关闭 DashScope 异步 HTTP 连接池
            await self.captioner.close_session()
        
        # 按段落分割
        paragraphs = re.split(r'\n\s*\n', cleaned_text.strip())
        
        blocks = []
        for idx, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue

            # ★ 中文噪声清洗：去除汉字间异常空格
            para = self._clean_chinese_spaces(para)

            # ★ 检查段落中是否包含图片占位符
            img_matches = re.findall(r'\[IMG_(\d+)\]', para)
            contained_images = []
            for img_idx in img_matches:
                img_info = next((i for i in image_paths if str(i["index"]) == img_idx), None)
                if img_info:
                    contained_images.append(img_info)
                    # ★ 将图片描述注入到文本中（用于 RAG 检索）
                    if img_info.get("caption"):
                        para = para.replace(f"[IMG_{img_idx}]", f"[图片: {img_info['caption']}]")

            is_heading = para.startswith("#")
            is_code = '<CODE_BLOCK>' in para
            is_table = self._is_table(para)
            clean_para = para.replace('<CODE_BLOCK>', '').replace('</CODE_BLOCK>', '')

            block_meta = {
                "block_index": idx,
                "is_heading": is_heading,
                "is_code": is_code,
                "is_table": is_table,  # ★ 标记表格块，供下游切块器保护
                "char_count": len(clean_para),
                "contains_images": [i["index"] for i in contained_images],  # ★ 关联图片索引
                "image_references": [   # ★ 新增：在 metadata 中保留图片原始信息（URL + alt）
                    {
                        "index": i["index"],
                        "url": i["url"],
                        "alt": i["alt"],
                        "caption": i.get("caption", ""),
                    }
                    for i in contained_images
                ],
            }
            if doc_title:
                block_meta["title"] = doc_title  # ★ 文档标题传入每个块，供 VectorStore 使用

            blocks.append({
                "page_num": 1,
                "text": clean_para,
                "metadata": block_meta,
            })
        
        # ★ 返回两个值：块列表 + 文档级元数据（包含所有图片信息）
        return blocks, clean_metadata
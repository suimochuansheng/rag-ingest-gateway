# src/cleaners/markdown_cleaner.py
import re
from typing import Tuple, Dict, Any,List

class MarkdownCleaner:
    def __init__(self):
        self.image_paths: List[Dict] = []  # 存储所有图片信息
        self.image_counter = 0

    async def clean(self, text: str) -> Tuple[str, Dict[str, Any]]:
        """
        异步清洗入口：适配 MarkdownLoader 的接口约定。
        输入: 原始 markdown 文本 (str)
        输出: (清洗后的 markdown 文本, 清洗元数据)
        """
        clean_metadata = {
            "stripped_frontmatter_count": 0,
            "html_comments_removed_count": 0,
            "images_converted_count": 0,
            "copyright_removed": False,
            "todo_removed": False,
        }

        # --- Layer 1: 格式清洗层 ---
        # 1.1 移除 YAML Frontmatter (包括有内容的和空的 --- 块)
        # 支持 sample.md 中的 "--- title: test ..." 以及空行包裹的 "--- \n title... \n ---"
        text, count = re.subn(
            r"(?:\n|^)---[^\n]*\n(.*?)\n---(?:\s*\n|$)", 
            "\n\n", 
            text, 
            flags=re.DOTALL
        )
        clean_metadata["stripped_frontmatter_count"] += count

        # 1.2 移除 HTML 注释 (包含单行及多行 <!-- ... -->)
        text, count = re.subn(r"<!--.*?-->", "", text, flags=re.DOTALL)
        clean_metadata["html_comments_removed_count"] += count

        # 1.3 提取图片路径并移除图片标记（仅保留 alt 文本）
        self.image_paths = []  # 重置
        self.image_counter = 0

        def extract_image(match):
            alt = match.group(1).strip()
            url = match.group(2).strip()
            if alt or url:
                # ★ 记录图片信息（包含顺序索引，用于定位）
                self.image_paths.append({
                    "alt": alt,
                    "url": url,
                    "index": self.image_counter,
                    "position": f"img_{self.image_counter}"  # 唯一标记
                })
            # ★ 返回占位符，留在文本流中标记位置
            placeholder = f"[IMG_{self.image_counter}]"
            if alt:
                placeholder += f": {alt}"
            
            self.image_counter += 1
            return placeholder
        
        # extract_image中的match 参数是由 re.sub 在内部自动传递的，不需要你手动传参
        text, img_count = re.subn(r"!\[(.*?)\]\((.*?)\)", extract_image, text)
        clean_metadata["images_converted_count"] = img_count
        clean_metadata["image_paths"] = self.image_paths  # 全局图片列表

        # 1.4 移除裸 URL 链接
        text = re.sub(r"(?<!\()(https?://[^\s)]+)", "", text)

        # --- Layer 2: 结构标准化层 ---
        # 2.1 统一标题格式：在紧贴的 '#' 后强制添加空格 (e.g. #标题 -> # 标题)
        text = re.sub(r"^(#{1,6})([^\s#])", r"\1 \2", text, flags=re.MULTILINE)

        # 2.2 识别并包裹 LaTeX 公式块 (可在此处扩展公式支持)
        text = re.sub(r"\$\$(.*?)\$\$", r"[formula]\1[/formula]", text, flags=re.DOTALL)


        # --- Layer 3: 规则过滤层 ---
        # 3.1 删除版权声明
        copyright_pattern = r"(Copyright\s*\d{4}\s*All\s*Rights\s*Reserved\s*.*?|版权所有.*?)"
        text, count = re.subn(copyright_pattern, "", text, flags=re.IGNORECASE)
        if count > 0:
            clean_metadata["copyright_removed"] = True

        # 3.2 删除 TODO 占位符
        todo_pattern = r"\[TODO:.*?\]"
        text, count = re.subn(todo_pattern, "", text, flags=re.IGNORECASE)
        if count > 0:
            clean_metadata["todo_removed"] = True


        # --- Layer 4: 代码块标记层 (适配 Loader) ---
        # 将 ``` 转换为 Loader 期望的 <CODE_BLOCK> 标签，并用双换行隔开，以便 Loader 按段落切分
        def replace_code_block(match):
            code_body = match.group(2).strip()
            # 前后补充双换行，确保 Loader 的 re.split(r'\n\s*\n') 能将其切分为独立 paragraph
            return f"\n\n<CODE_BLOCK>\n{code_body}\n</CODE_BLOCK>\n\n"

        text = re.sub(r"```([a-zA-Z0-9-]*)\n(.*?)\n```", replace_code_block, text, flags=re.DOTALL)


        # --- 终期收尾：合并多余空行并去除两端空格 ---
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        return text, clean_metadata
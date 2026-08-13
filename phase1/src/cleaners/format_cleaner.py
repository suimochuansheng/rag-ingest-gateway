# 格式层 layer1
import re
from typing import Dict, Any
from .base import Cleaner

class FormatCleaner(Cleaner):
    """格式层清洗：Frontmatter、注释、图片链接、URL"""
    
    async def clean(self, text: str, metadata: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        # 1. 剥离 YAML Frontmatter (--- ... ---)
        text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL)
        
        # 2. 移除 HTML 注释 <!-- ... -->
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
        
        # 3. 图片链接 → 仅保留 alt 文本
        # ![alt](url) → alt
        text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
        
        # 4. 移除裸 URL (保留纯文本)
        text = re.sub(r'https?://[^\s<>"]+', '', text)
        
        # 5. 移除行内代码标记 ` 但保留内容（不删除，只去标记）
        # 注意：这里不处理代码块 ```，交给后续层
        text = re.sub(r'`([^`]+)`', r'\1', text)
        
        metadata["format_cleaned"] = True
        return text, metadata
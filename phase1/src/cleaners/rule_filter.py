# 规则层   layer3
import re
from typing import Dict, Any
from .base import Cleaner

class RuleFilter(Cleaner):
    """规则层过滤：版权声明、测试占位符、空行"""
    
    # 版权声明模式（可配置）
    COPYRIGHT_PATTERNS = [
        r'©\s*\d{4}.*All rights reserved.*',
        r'Copyright\s*©?\s*\d{4}.*',
        r'本作品受.*版权保护.*',
        r'License:.*MIT.*',
        r'Apache License.*',
    ]
    
    # 测试占位符模式
    PLACEHOLDER_PATTERNS = [
        r'\[TODO:.*?\]',
        r'\[内容缺失.*?\]',
        r'TODO\s*:.*',
        r'FIXME\s*:.*',
        r'\[待补充.*?\]',
    ]
    
    async def clean(self, text: str, metadata: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        lines = text.split('\n')
        cleaned_lines = []
        
        for line in lines:
            # 跳过空行（保留一个空行作为段落分隔，但连续空行压缩）
            if not line.strip():
                continue
            
            # 检查版权声明
            is_copyright = False
            for pattern in self.COPYRIGHT_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    is_copyright = True
                    break
            if is_copyright:
                metadata["removed_copyright"] = True
                continue
            
            # 检查测试占位符
            is_placeholder = False
            for pattern in self.PLACEHOLDER_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    is_placeholder = True
                    metadata["removed_placeholders"] = True
                    break
            if is_placeholder:
                continue
            
            cleaned_lines.append(line)
        
        metadata["rule_filtered"] = True
        return '\n\n'.join(cleaned_lines), metadata  # 恢复单空行分隔
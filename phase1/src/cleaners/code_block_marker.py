# 代码标记层（简化版）  layer4
import re
from typing import Dict, Any, List
from .base import Cleaner

class CodeBlockMarker(Cleaner):
    """代码块标记：识别代码块，标记 metadata"""
    
    async def clean(self, text: str, metadata: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        lines = text.split('\n')
        cleaned_lines = []
        in_code_block = False
        code_block_content = []
        has_code_block = False
        
        for line in lines:
            if line.strip().startswith('```'):
                if in_code_block:
                    # 代码块结束
                    has_code_block = True
                    # 将代码块内容合并为一行（用空格分隔），添加标记
                    code_text = ' '.join(code_block_content).strip()
                    if code_text:
                        cleaned_lines.append(f"<CODE_BLOCK>{code_text}</CODE_BLOCK>")
                    code_block_content = []
                in_code_block = not in_code_block
                continue
            
            if in_code_block:
                code_block_content.append(line.strip())
                continue
            
            # 非代码块内容直接保留
            cleaned_lines.append(line)
        
        metadata["has_code_blocks"] = has_code_block
        metadata["code_block_count"] = len([c for c in cleaned_lines if '<CODE_BLOCK>' in c])
        metadata["code_block_marked"] = True
        
        return '\n'.join(cleaned_lines), metadata
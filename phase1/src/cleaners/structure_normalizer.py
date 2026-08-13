# 结构层  layer2
import re
from typing import Dict, Any, List
from .base import Cleaner

class StructureNormalizer(Cleaner):
    """结构层标准化：标题、列表、表格、LaTeX"""
    
    async def clean(self, text: str, metadata: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        lines = text.split('\n')
        cleaned_lines = []
        in_code_block = False
        formula_blocks = []
        
        for i, line in enumerate(lines):
            # 跳过代码块内部（代码块内容保持原样）
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                cleaned_lines.append(line)
                continue
            
            if in_code_block:
                cleaned_lines.append(line)
                continue
            
            # 1. 标题规范化：# 标题 → 确保 # 后有空格
            if re.match(r'^#{1,6}\S', line):
                line = re.sub(r'^(#{1,6})(\S)', r'\1 \2', line)
            
            # 2. LaTeX 公式块识别与标记
            if re.match(r'^\$\$', line) or line.strip().endswith('$$'):
                formula_blocks.append(i)
                # 保留公式内容，添加 metadata 标记（在 metadata 中记录位置）
                line = f"<FORMULA>{line.strip()}</FORMULA>"
            
            # 3. 列表缩进统一（检测缩进列表）
            # 将任意缩进的列表统一为 2 空格缩进
            list_match = re.match(r'^(\s*)([-*+]|\d+\.)\s+(.*)', line)
            if list_match:
                indent = len(list_match.group(1))
                # 每级缩进对应 2 空格（子列表比父列表缩进多）
                if indent > 0:
                    # 计算层级（按2空格为一个层级）
                    level = indent // 2
                    new_indent = '  ' * level
                    line = f"{new_indent}{list_match.group(2)} {list_match.group(3)}"
                # 行首不需要缩进（一级列表）
                else:
                    line = f"{list_match.group(2)} {list_match.group(3)}"
            
            # 4. 表格标准化（移除多余竖线）
            if '|' in line and not line.strip().startswith('|'):
                # 确保表格每行以 | 开头和结尾
                parts = line.split('|')
                # 如果表格分隔线 --- 不处理
                if '---' not in line:
                    line = '| ' + ' | '.join([p.strip() for p in parts if p.strip()]) + ' |'
            
            cleaned_lines.append(line)
        
        metadata["has_formula"] = bool(formula_blocks)
        metadata["formula_positions"] = formula_blocks
        metadata["structure_normalized"] = True
        
        return '\n'.join(cleaned_lines), metadata
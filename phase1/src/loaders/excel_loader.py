# /rag_data_dispose/phase1/src/loaders/excel_loader.py
import pandas as pd
from io import BytesIO
from typing import List, Dict, Any
from .base import DocumentLoader

class ExcelLoader(DocumentLoader):
    async def load(self, content: bytes) -> List[Dict[str, Any]]:
        """
        解析 Excel (.xlsx) 文件：
        1. 读取所有 Sheet
        2. 每行数据转为 "列名:值" 的键值对文本
        3. 保留 Sheet 名称和行号作为元数据
        """
        blocks = []
        
        # 读取所有 sheet
        excel_data = pd.read_excel(BytesIO(content), sheet_name=None, engine="openpyxl")
        
        for sheet_name, df in excel_data.items():
            # 过滤全空行
            df = df.dropna(how="all")
            
            if df.empty:
                continue
            
            # 将每行转换为文本描述
            for idx, row in df.iterrows():
                # 构建键值对字符串
                parts = []
                for col, val in row.items():
                    if pd.notna(val):
                        # 处理日期等特殊类型
                        if isinstance(val, pd.Timestamp):
                            val = val.strftime("%Y-%m-%d")
                        parts.append(f"{col}:{val}")
                
                if not parts:
                    continue
                
                row_text = f"表格[{sheet_name}]第{idx+1}行: " + ", ".join(parts)
                blocks.append({
                    "page_num": 1,
                    "text": row_text,
                    "metadata": {
                        "sheet": sheet_name,
                        "row": idx + 1,
                        "col_count": len(parts)
                    }
                })
        
        return blocks, {}
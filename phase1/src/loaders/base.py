from abc import ABC, abstractmethod
from typing import List, Dict, Any

class DocumentLoader(ABC):
    """所有文档加载器的抽象基类
        所有用来读取文档、拆文本块的代码，都要继承这个 DocumentLoader，保证输出格式完全统一，上层检索、分块、向量化代码不用区分文件类型。
    """
    
    @abstractmethod
    async def load(self, content: bytes) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        将二进制内容解析为 (标准化块列表, 文档级元数据)。
        每个块格式：{"page_num": int, "text": str, "metadata": dict}
        """
        pass
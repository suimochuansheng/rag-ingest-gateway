# 清洗器抽象基类
from abc import ABC, abstractmethod
from typing import Any


class Cleaner(ABC):
    """所有清洗器的抽象基类"""

    @abstractmethod
    async def clean(self, text: str, metadata: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """
        清洗方法，返回 (清洗后的文本, 更新后的元数据)
        """
        pass

# 集中管理所有日志配置，导出统一的日志记录器。
# src/logger.py
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 1. 确定项目根目录（rag-ingest-gateway）
# 当前文件在 rag-ingest-gateway/phase1/src/logger.py
# .parent.parent 回到 phase1，再 .parent 回到 rag-ingest-gateway
PROJECT_ROOT = Path(__file__).parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
print(f"日志目录: {LOG_DIR.absolute()}")  # 打印日志目录，便于调试

# 2. 创建 logs 目录（如果不存在）
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "rag_ingest.log"

# 3. 定义统一格式（含文件名、行号、函数名）
_FORMATTER = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def _setup_root_logger():
    """配置根日志记录器（仅执行一次）"""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # # 如果需要精细调试httpx等第三方库，可以设置为DEBUG 
    # root_logger.setLevel(logging.DEBUG)
    # 降低第三方 HTTP 库的日志级别，只显示 WARNING 及以上
    # logging.getLogger("httpx").setLevel(logging.WARNING)
    # logging.getLogger("httpcore").setLevel(logging.WARNING)

    # ★★★ 防重复添加 Handler（解决热重载/多次导入问题）★★★
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # 控制台 Handler（INFO 及以上，避免刷屏）
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(_FORMATTER)
    root_logger.addHandler(console)

    # 文件 Handler（DEBUG 及以上，含详细堆栈，10MB 轮转）
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    # 设置文件日志级别为 DEBUG，记录详细信息
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_FORMATTER)
    root_logger.addHandler(file_handler)

# 4. 主动执行初始化（当模块被导入时自动配置）
_setup_root_logger()

def get_logger(name: str) -> logging.Logger:
    """
    获取已配置好的日志记录器。
    用法：logger = get_logger(__name__)
    """
    return logging.getLogger(name)
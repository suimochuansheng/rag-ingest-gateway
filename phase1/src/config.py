"""rag-ingest-gateway 配置模块。

通过 pydantic-settings 从 .env 文件加载环境变量，提供类型安全的全局配置访问。
"""

from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """项目全局配置，自动从 .env 文件和环境变量中加载。

    使用 case_sensitive=True 保持与 .env 中大写变量名的一致映射，
    避免大规模重构已有代码中的 settings.POSTGRES_HOST 等引用。
    """

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # 忽略 .env 中未在 Settings 定义的变量（如 ALI_* / OLLAMA_MODEL_NAME）
    )

    # ── PostgreSQL ──────────────────────────────────────────
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "langgraph_user"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "langgraph_db"

    # ── Ollama ──────────────────────────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    EMBEDDING_MODEL: str = Field(
        alias="OLLAMA_EMBEDDING_MODEL",
        # nomic-embed-text/bge-m3
        default="nomic-embed-text",
        description=".env 中变量名为 OLLAMA_EMBEDDING_MODEL，保持代码侧 EMBEDDING_MODEL 不变",
    )
    EMBEDDING_DIM: int = 768
    
    # ── Chunking ────────────────────────────────────────────
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 100

    # ── 检索 ────────────────────────────────────────────────
    TOP_K: int = 3
    SCORE_THRESHOLD: float = 0.65  # 相似度阈值，低于此值的结果将被过滤

    # ── PDF 解析 ────────────────────────────────────────────
    PDF_ENABLE_OCR: bool = False
    PDF_OCR_LANG: str = "chi_sim+eng"
    PDF_OCR_CHARS_THRESHOLD: int = 15
    PDF_OCR_IMAGE_RATIO_THRESHOLD: float = 0.3
    PDF_COLUMN_GAP_RATIO: float = 0.1
    PDF_MIN_WORDS_PER_COLUMN: int = 5
    PDF_Y_TOLERANCE: float = 3.0

    # ── 派生字段 ────────────────────────────────────────────
    @computed_field  # type: ignore[prop-decorator]
    @property
    def POSTGRES_DSN(self) -> str:
        """由各 PG 字段自动组装的连接字符串。"""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )
    # ── reranker ────────────────────────────────────────────
    RERANKER_NAME: str = ""
    RERANKER_CACHE_DIR: str = "/tmp/flashrank_cache"

    # ── Sentry 崩溃自动捕获 ────────────────────────────────
    SENTRY_DSN: str = ""
    ENV: str = "dev"
    

# 全局单例，项目各处从此导入
settings = Settings()

import logging
import re
from collections import defaultdict
from typing import Any

import psycopg
from config import settings
from pgvector.psycopg import register_vector_async

# 配置日志格式（时间 + 级别 + 消息）
logger = logging.getLogger(__name__)


def _chinese_bigram_tokenize(text: str) -> str:
    """将中文文本预处理为字符级 bigram（空格分隔），提升 PostgreSQL simple 分词的 BM25 效果。

    PostgreSQL 的 simple 分词配置不会对无空格的中文文本进行切分，
    导致整个中文段落被视为单一 token。此函数将连续中文字符拆为 bigram，
    非中文部分（英文/数字/标点）保留原样，让 simple 分词器能正确切词。

    Args:
        text: 原始文本（可包含中英文混合内容）

    Returns:
        空格分隔的预处理后文本

    Example:
        >>> _chinese_bigram_tokenize("你好世界 test")
        "你好 好世 世界 test"
    """
    result: list[str] = []
    # 匹配连续中文、非中文块
    for chunk in re.split(r"([\u4e00-\u9fff\u3400-\u4dbf]+)", text):
        if not chunk:
            continue
        if re.match(r"[\u4e00-\u9fff\u3400-\u4dbf]", chunk[0]):
            # 中文块 → 生成 bigram
            for i in range(len(chunk) - 1):
                result.append(chunk[i] + chunk[i + 1])
            if len(chunk) == 1:
                result.append(chunk)
        else:
            result.append(chunk)
    return " ".join(result)


class VectorStore:
    def __init__(self,table_suffix: str = ""):
        """
        Args:
            table_suffix: 表名后缀，用于区分不同 Embedding 模型
                         - "" (默认) → knowledge_embeddings (768维, nomic-embed)
                         - "_bgem3" → knowledge_embeddings_bgem3 (1024维, bge-m3)
        """
        self.dsn = settings.POSTGRES_DSN
        self.table_suffix = table_suffix
        self.table_name = f"knowledge_embeddings{table_suffix}"

        # 根据表名确定向量维度
        if "bgem3" in table_suffix:
            self.vector_dim = 1024
        else:
            self.vector_dim = 768

    async def _get_connection(self):
        """获取异步数据库连接"""
        conn = await psycopg.AsyncConnection.connect(self.dsn)
        await conn.set_autocommit(True)

        # 2. 先创建 extension，确保数据库中已存在 vector 类型
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

        # 3. 使用 await 异步注册 vector 类型
        await register_vector_async(conn)

        return conn

    async def ensure_pipeline_jobs_table(self):
        """创建 pipeline_jobs 任务状态表（独立于 knowledge_embeddings）
           作用 ：任务状态与控制表（审计 + 异步任务管理）
                用于解决“大文件阻塞”、“审计门禁”和“HITL 审批”等企业级痛点，
                记录每一次文档摄入任务的完整生命周期：上传、解析、审计、审批、入库、失败等。
        """
        async with await self._get_connection() as conn:
            await conn.execute(""" 
                CREATE TABLE IF NOT EXISTS pipeline_jobs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    kb_id VARCHAR(50) NOT NULL DEFAULT 'default',
                    source_file TEXT NOT NULL,
                    file_sha256 TEXT NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                    risk_flags JSONB DEFAULT '[]',
                    estimated_tokens INT,
                    error_message TEXT,
                    snapshot_path TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_sha256 ON pipeline_jobs(file_sha256)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_status ON pipeline_jobs(status)")

    async def ensure_table(self):
        """确保 knowledge_embeddings 表存在（支持动态表名和向量维度）"""
        async with await self._get_connection() as conn, conn.cursor() as cur:
            await cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self.table_name} (
                        id SERIAL PRIMARY KEY,
                        title TEXT,
                        content TEXT NOT NULL,
                        embedding vector({self.vector_dim}),
                        source_file TEXT,
                        kb_id VARCHAR(50) DEFAULT 'default',
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
            await cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_kb_id_{self.table_suffix or 'default'} 
                    ON {self.table_name}(kb_id)
                """)
            await cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_embedding_{self.table_suffix or 'default'} 
                    ON {self.table_name} 
                    USING ivfflat (embedding vector_cosine_ops) 
                    WITH (lists = 10)
                """)
            logger.info(f"✅ 表 {self.table_name} 已就绪 (维度: {self.vector_dim})")

    async def insert_many(
        self,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
        source_file: str,
        kb_id: str = "default"
    ):
        """
        批量插入文本块及其向量到 PostgreSQL，支持动态表名和向量维度。
        Args:
            chunks: 每个 chunk 是一个字典，包含 'content' 和 'metadata'
            embeddings: 与 chunks 对应的向量列表
            source_file: 来源文件名，用于追踪
            kb_id: 知识库 ID，用于分区检索
        """
        if len(chunks) != len(embeddings):
            raise ValueError(...)

        await self.ensure_table()

        async with await self._get_connection() as conn:
            async with conn.cursor() as cur:
                for chunk, emb in zip(chunks, embeddings):
                    if not emb or len(emb) != self.vector_dim:
                        logger.warning(f"⚠️ 跳过无效向量: {len(emb) if emb else 0} 维 (期望 {self.vector_dim})")
                        continue

                    content = chunk.get("content", "")
                    title = chunk.get("metadata", {}).get("title", source_file)

                    # 中文 bigram 预处理后生成 tsvector，提升 BM25 对中文的检索效果
                    processed_content = _chinese_bigram_tokenize(content)
                    await cur.execute(f"""
                        INSERT INTO {self.table_name}
                            (title, content, embedding, source_file, kb_id, content_tsv)
                        VALUES (%s, %s, %s, %s, %s, to_tsvector('simple', %s))
                    """, (title, content, emb, source_file, kb_id, processed_content))

    async def search(
        self,
        query_vector: list[float],
        kb_id: str | None = None,
        top_k: int = 3,
        score_threshold: float | None = None
    ) -> list[dict]:
        """相似度检索，支持动态表名"""
        if score_threshold is None:
            score_threshold = getattr(settings, 'SCORE_THRESHOLD', 0.65)

        # 构建 WHERE 子句
        where_clause = ""
        params = [query_vector]

        if kb_id:
            where_clause = f"WHERE {self.table_name}.kb_id = %s"
            params.append(kb_id)

        # 使用 CTE 查询
        sql = f"""
            WITH ranked AS (
                SELECT id, content, source_file, kb_id,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM {self.table_name}
                {where_clause}
            )
            SELECT * FROM ranked
            WHERE similarity >= %s
            ORDER BY similarity DESC
            LIMIT %s
        """
        params.extend([score_threshold, top_k])

        async with await self._get_connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

            return [
                {
                    "id": row[0],
                    "content": row[1],
                    "source_file": row[2],
                    "kb_id": row[3],
                    "similarity": row[4],
                }
                for row in rows
            ]

    async def _bm25_search(
        self,
        query: str,
        kb_id: str | None = None,
        top_k: int = 10,
    ) -> list[dict]:
        """基于 PostgreSQL tsvector 的 BM25 全文检索。

        对查询文本执行与入库时相同的中文 bigram 预处理，
        再用 plainto_tsquery('simple', ...) 构建查询，
        与 content_tsv 列做 @@ 匹配，按 ts_rank 排序。

        Args:
            query: 用户原始查询文本
            kb_id: 知识库 ID（可选过滤）
            top_k: 返回结果数量

        Returns:
            匹配结果列表，每项含 id/content/source_file/kb_id/bm25_score
        """
        processed_query = _chinese_bigram_tokenize(query)
        sql = f"""
            SELECT id, content, source_file, kb_id,
                   ts_rank(content_tsv, plainto_tsquery('simple', %s)) AS bm25_score
            FROM {self.table_name}
            WHERE content_tsv @@ plainto_tsquery('simple', %s)
        """
        params = [processed_query, processed_query]
        if kb_id:
            sql += " AND kb_id = %s"
            params.append(kb_id)
        sql += " ORDER BY bm25_score DESC LIMIT %s"
        params.append(top_k)

        async with await self._get_connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
            return [
                {
                    "id": row[0],
                    "content": row[1],
                    "source_file": row[2],
                    "kb_id": row[3],
                    "bm25_score": row[4],
                }
                for row in rows
            ]

    async def hybrid_search(
        self,
        query: str,
        query_vector: list[float],
        kb_id: str | None = None,
        top_k: int = 3,
        score_threshold: float = 0.65,
        rrf_k: int = 60,
    ) -> list[dict]:
        """混合检索：BM25 全文检索 + 向量语义检索，通过 RRF 融合结果。

        1. 向量检索（Top-10，放宽阈值保证候选集）
        2. BM25 全文检索（Top-10）
        3. RRF (Reciprocal Rank Fusion) 融合分数
        4. 按 RRF 分数排序取 Top-K

        Args:
            query: 用户自然语言查询
            query_vector: 查询 Embedding 向量
            kb_id: 知识库 ID（可选过滤）
            top_k: 最终返回结果数量
            score_threshold: 向量检索相似度阈值
            rrf_k: RRF 公式中的 k 参数（默认 60）

        Returns:
            融合后的检索结果列表，每项包含 rrf_score 和原始字段
        """
        # 1. 向量检索（放宽阈值保证足够候选）
        vector_results = await self.search(
            query_vector,
            kb_id=kb_id,
            top_k=10,
            score_threshold=0.3,
        )
        # 2. BM25 全文检索
        bm25_results = await self._bm25_search(query, kb_id=kb_id, top_k=10)

        # 3. RRF 融合
        scores: defaultdict[int, float] = defaultdict(float)
        for rank, r in enumerate(vector_results, 1):
            scores[r["id"]] += 1.0 / (rrf_k + rank)
        for rank, r in enumerate(bm25_results, 1):
            scores[r["id"]] += 1.0 / (rrf_k + rank)

        # 4. 按 RRF 分数排序取 Top-K
        sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)[:top_k]

        # 5. 构建结果（优先从向量结果取，其次从 BM25 结果补）
        id_to_item: dict = {r["id"]: r for r in vector_results}
        for r in bm25_results:
            if r["id"] not in id_to_item:
                id_to_item[r["id"]] = r
        for item in id_to_item.values():
            item["rrf_score"] = round(scores.get(item["id"], 0), 6)

        return [id_to_item[id] for id in sorted_ids]

    # ── pipeline_jobs 任务状态管理 ──────────────────────────────

    async def create_task(self, task_id: str, kb_id: str, source_file: str, status: str = "PENDING"):
        """创建任务记录（pipeline_jobs 表不变）"""
        async with await self._get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO pipeline_jobs (id, kb_id, source_file, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, NOW(), NOW())
                """, (task_id, kb_id, source_file, status))

    async def get_task_status(self, task_id: str) -> dict | None:
        """查询任务状态"""
        async with await self._get_connection() as conn, conn.cursor() as cur:
            await cur.execute("""
                    SELECT id, kb_id, source_file, status, error_message, created_at, updated_at
                    FROM pipeline_jobs
                    WHERE id = %s
                """, (task_id,))
            row = await cur.fetchone()
            if row:
                return {
                    "task_id": row[0],
                    "kb_id": row[1],
                    "source_file": row[2],
                    "status": row[3],
                    "error_message": row[4],
                    "created_at": row[5],
                    "updated_at": row[6]
                }
            return None

    async def update_task_status(self, task_id: str, status: str, error_message: str | None = None):
        """更新任务状态"""
        async with await self._get_connection() as conn, conn.cursor() as cur:
            if error_message:
                await cur.execute("""
                        UPDATE pipeline_jobs 
                        SET status = %s, error_message = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (status, error_message, task_id))
            else:
                await cur.execute("""
                        UPDATE pipeline_jobs 
                        SET status = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (status, task_id))

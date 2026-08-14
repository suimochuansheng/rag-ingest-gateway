# RAG Ingest Gateway

知识库摄入与检索网关服务（AdaptiveSearchAgent 的 RAG 子服务）。

## 技术栈

- FastAPI + Uvicorn
- PostgreSQL (pgvector)
- Ollama (Embedding)
- Poetry

## 快速启动

### 独立运行（本地）

```bash
poetry install
poetry run uvicorn api_main:app --reload --port 8100
```

### 通过 Docker Compose（推荐）

在父目录 `dev_pros` 执行：

```bash
docker compose up -d
```

服务地址：http://localhost:8100

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /health | 健康检查 |
| POST | /api/v1/ingest | 摄入知识文档 |
| POST | /api/v1/search | 检索知识库 |
| GET | /api/v1/tasks/{task_id} | 查询摄入任务状态 |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| POSTGRES_HOST | 数据库主机 | localhost |
| POSTGRES_USER | 数据库用户 | langgraph_user |
| POSTGRES_DB | 数据库名 | langgraph_db |
| OLLAMA_BASE_URL | Ollama 服务地址 | http://localhost:11434 |

## 测试

```bash
poetry run pytest tests/
```

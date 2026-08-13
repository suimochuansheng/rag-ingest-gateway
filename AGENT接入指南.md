# RAG 数据处置引擎 — AI Agent 完整接入指南

> **目标读者**：新接入的 AI 编程 Agent。
> **承诺**：读完本文档即可掌握项目全貌、所有公开函数、数据流和架构，无需翻阅代码。
> **生成时间**：2026-07-08 | **版本**：Phase1 v1.1

---

## 目录

1. [项目身份](#1-项目身份)
2. [技术栈速览](#2-技术栈速览)
3. [整体架构](#3-整体架构)
4. [完整文件结构](#4-完整文件结构)
5. [公开函数/类 API 参考](#5-公开函数类-api-参考)
   - [5.1 入口层](#51-入口层)
   - [5.2 日志与数据库初始化](#52-日志与数据库初始化)
   - [5.3 Loader 层（数据输入）](#53-loader-层数据输入)
   - [5.4 Cleaner 层（清洗）](#54-cleaner-层清洗)
   - [5.5 Vision 层（AI 增强）](#55-vision-层ai-增强)
   - [5.6 Chunking 层（切分）](#56-chunking-层切分)
   - [5.7 Embedding 层（向量化）](#57-embedding-层向量化)
   - [5.8 Storage 层（持久化）](#58-storage-层持久化)
   - [5.9 Snapshot（可观测）](#59-snapshot可观测)
6. [请求生命周期](#6-请求生命周期)
7. [上游接入方式](#7-上游接入方式)
8. [配置参考](#8-配置参考)
9. [基础设施依赖](#9-基础设施依赖)
10. [编码规范摘要](#10-编码规范摘要)
11. [快速检查清单](#11-快速检查清单)

---

## 1. 项目身份

| 字段 | 值 |
|------|-----|
| 项目名 | rag-ingest-gateway |
| 简称 | rag-ingest |
| 定位 | RAG 前置数据清洗与向量化引擎：文档摄入 → 清洗 → 图片描述 → 切块 → 向量化 → pgvector 入库 |
| Python | 3.11+ |
| 包管理 | pip（`pip install -r requirements.txt`） |
| 项目路径 | `/home/ubhuazhu/dev_pros/rag-ingest-gateway/phase1/` |
| 启动命令 | `python scripts/ingest_knowledge.py --file <文档路径> --kb_id <知识库ID>` |

---

## 2. 技术栈速览

### 核心框架

| 依赖 | 用途 |
|------|------|
| `langchain-text-splitters` | RecursiveCharacterTextSplitter 语义切块 |
| `dashscope` | 阿里云百炼 DashScope SDK（Qwen-VL 图片描述，异步 + 本地文件自动上传 OSS）|
| `aiohttp` | 异步 HTTP 会话（DashScope 多模态调用） |
| `httpx` | 异步 HTTP 客户端（Ollama 嵌入 API 调用） |
| `psycopg` + `pgvector` | PostgreSQL 异步连接 + 向量操作（`register_vector_async`） |
| `pydantic-settings` | 类型安全的 `.env` 配置管理 |

### 文档解析

| 依赖 | 支持的格式 |
|------|-----------|
| `pdfplumber` | PDF（含列感知提取 + Markdown 表格输出 + OCR 降级） |
| `python-docx` | Word (.docx) |
| `pandas` + `openpyxl` | Excel (.xlsx) |
| 内置 `re` | Markdown / TXT（含自定义清洗管道） |

### 可选 OCR 依赖（PDF 扫描件识别）

| 依赖 | 用途 |
|------|------|
| `pdf2image` | PDF 页面转图像 |
| `pytesseract` | OCR 文字识别（需系统安装 tesseract + poppler） |

### 配置与可观测

| 依赖 | 用途 |
|------|------|
| `python-dotenv` | `.env` 文件加载（pydantic-settings 自动读取） |
| `asyncio` | 全链路异步 |
| `logging` + `RotatingFileHandler` | 统一日志（控制台 + 文件轮转，10MB/5备份） |
| `json` + `pathlib` | 快照持久化 + 审计日志 |

---

## 3. 整体架构

### 3.1 系统组成

```
┌──────────────────────────────────────────────────────────────┐
│                  CLI / 上游对话助手                             │
│     python scripts/ingest_knowledge.py --file doc.pdf          │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────┐
│                     ingest_knowledge.py                        │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  main(file_path, kb_id, fail_on_caption_error)           │ │
│  │                                                          │ │
│  │  1. 文件读取 → bytes，生成 run_id (UUID)                  │ │
│  │  2. 后缀路由 → Loader 选型（含 PDF OCR 配置注入）          │ │
│  │  3. loader.load() → (blocks, doc_metadata)               │ │
│  │  4. PipelineSnapshot.save("after_load")                  │ │
│  │  5. ensure_all_tables()  ← 建表 + pipeline_jobs 任务表    │ │
│  │  6. run_full_pipeline():                                 │ │
│  │     ├─ chunker.chunk() → chunks                          │ │
│  │     ├─ PipelineSnapshot.save("after_chunk")              │ │
│  │     ├─ embedder.embed_batch() → embeddings               │ │
│  │     ├─ PipelineSnapshot.save("after_embed")              │ │
│  │     ├─ store.insert_many() → pgvector                    │ │
│  │     └─ store.search() → 检索验证                         │ │
│  │  7. 审计日志 (JSON)                                       │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ DashScope│ │  Ollama  │ │PostgreSQL│
   │ Qwen-VL  │ │ nomic-   │ │ pgvector │
   │ 图片描述  │ │ embed    │ │ 向量库   │
   └──────────┘ └──────────┘ └──────────┘
```

### 3.2 Pipeline 数据流

```
原始文档 bytes
  │
  ▼
Loader 加载 → (blocks, doc_metadata)
  │              │
  │              ├─ Markdown: 经过 MarkdownCleaner 清洗
  │              │              + ImageCaptioner 图片描述
  │              │              + 自动提取 # 标题
  │              │
  │              ├─ PDF: pdfplumber 列感知提取
  │              │        + 表格转 Markdown
  │              │        + 标题启发式注入
  │              │        + 扫描件 OCR 降级
  │              │
  │              └─ Word/Excel: 直接解析
  │
  ▼
PipelineSnapshot.save("after_load")    ← 统一快照
  │
  ▼
ensure_all_tables()                    ← 建表（含 pipeline_jobs 审计表）
  │
  ▼
SemanticChunker.chunk()
  │  blocks[].text → chunks[].content
  ▼
PipelineSnapshot.save("after_chunk")   ← 切块后快照
  │
  ▼
OllamaEmbedder.embed_batch()
  │  List[str] → List[List[float]] (768维)
  ▼
PipelineSnapshot.save("after_embed")   ← 嵌入后快照（仅存文本切片）
  │
  ▼
VectorStore.insert_many()
  │  chunks + embeddings → knowledge_embeddings 表
  ▼
PostgreSQL pgvector (IVF 索引, cosine 相似度)
  │
  ▼
检索验证: store.search(test_emb)       ← 自动验证首条结果
```

### 3.3 关键设计决策

| 决策 | 原因 |
|------|------|
| **基类统一返回 tuple** | `DocumentLoader.load()` → `tuple[List[Block], Dict[DocMeta]]`，全格式一致 |
| **PDF 升级为 pdfplumber** | 替代 pypdf，支持列感知提取、表格 Markdown 化、扫描件 OCR 降级 |
| **PDF 标题注入** | 启发式正则识别"1. xxx"、"一、xxx"等结构，注入 `## ` 标记供 Chunker 精准切分 |
| **清洗仅在 MarkdownLoader 内执行** | PDF/Word/Excel 的解析库已产出清洁文本，无需额外清洗管道 |
| **图片路径解析在 MarkdownLoader** | `base_dir` 参数从 `ingest_knowledge.py` 传入，用于将 `./images/x.jpg` 转绝对路径 |
| **DashScope 原生异步 SDK** | `AioMultiModalConversation`（非 OpenAI 兼容），本地文件自动上传 OSS，免带宽开销 |
| **RecursiveCharacterTextSplitter** | 中文友好分隔符 `["\n\n","\n","。","！","？","；","，"," ",""]`，512 字符切块 + 50 重叠 |
| **Ollama 串行嵌入** | 无批量 API，逐条调用；失败零向量 `[0.0]*768` 占位（实际应重试） |
| **pgvector IVF 索引** | `lists=10`，`vector_cosine_ops`，适合初期数据量 |
| **Pipeline Snapshot** | 旁路非侵入，JSON + TXT 双格式，覆盖 after_load / after_chunk / after_embed 三阶段 |
| **审计日志 + run_id** | 每次执行生成 UUID，结束时输出 JSON 审计记录到日志 |
| **pipeline_jobs 任务表** | 独立于 knowledge_embeddings，支持审计门禁、HITL 审批、大文件异步管理 |

---

## 4. 完整文件结构

```
rag-ingest-gateway/phase1/
├── scripts/
│   └── ingest_knowledge.py            # ★ CLI 入口主脚本（全链路已激活）

├── src/
│   ├── __init__.py
│   ├── config.py                       # ★ 全局配置（pydantic-settings，含 PDF 解析参数）
│   ├── logger.py                       # ★ 统一日志模块（RotatingFileHandler + 防重复）
│   ├── db_init.py                      # ★ 数据库初始化（建向量表 + pipeline_jobs 任务表）
│   │
│   ├── loaders/                        # [数据输入层]
│   │   ├── base.py                     #   ★ DocumentLoader 抽象基类
│   │   ├── pdf_loader.py              #   ★★ PDF 解析器（pdfplumber + OCR + 列感知 + 标题注入）
│   │   ├── docx_loader.py             #   ★ Word 解析器
│   │   ├── excel_loader.py            #   ★ Excel 解析器
│   │   └── markdown_loader.py         #   ★★ Markdown/TXT（含清洗调度+图片调度+标题提取）
│   │
│   ├── cleaners/                       # [清洗层] 仅 Markdown 使用
│   │   ├── base.py                     #   Cleaner 抽象基类（独立类，未被实际调用）
│   │   ├── markdown_cleaner.py        #   ★★ 4层统一清洗管道
│   │   ├── format_cleaner.py          #   Layer1 格式清洗（独立类，未被调用）
│   │   ├── structure_normalizer.py    #   Layer2 结构标准化（独立类，未被调用）
│   │   ├── rule_filter.py             #   Layer3 规则过滤（独立类，未被调用）
│   │   └── code_block_marker.py       #   Layer4 代码块标记（独立类，未被调用）
│   │
│   ├── vision/                         # [AI 增强层]
│   │   └── image_captioner.py         #   ★★ DashScope Qwen-VL 异步图片描述
│   │
│   ├── chunking/                       # [切分层]
│   │   └── semantic_chunker.py        #   ★ RecursiveCharacterTextSplitter 切块
│   │
│   ├── embedding/                      # [向量化层]
│   │   └── embedder.py                #   ★ Ollama 嵌入（768 维）
│   │
│   ├── storage/                        # [持久化层]
│   │   └── vector_store.py            #   ★ pgvector 写入 + IVF 索引 + pipeline_jobs + 检索
│   │
│   └── snapshot.py                     # ★★ Pipeline Snapshot（JSON + TXT）

├── snapshots/                          # 快照产物目录（运行时生成）
│   ├── after_load/                    #   Loader 产出后快照
│   ├── after_chunk/                   #   Chunking 后快照
│   └── after_embed/                   #   Embedding 后快照（仅存文本切片）

├── logs/                               # 日志目录（运行时生成）
│   └── rag_ingest.log                 #   轮转日志文件（10MB/5备份）

├── data/                               # 测试样本
│   ├── sample.md / .pdf / .docx / .xlsx
│   ├── test_with_images.md
│   ├── test_small.txt
│   ├── test_rag_spec.md
│   ├── test_rag_spec.pdf
│   └── images/team.jpg

└── .env                                # 环境变量
```

> 标注 ★ 为外部调用入口，★★ 为核心模块（改动最频繁）。

---

## 5. 公开函数/类 API 参考

### 5.1 入口层

#### `scripts/ingest_knowledge.py` ★ — CLI 主脚本

```python
async def main(
    file_path: str,
    kb_id: str = "default",
    fail_on_caption_error: bool = True,
) -> None:
    """
    Phase1 离线文档摄入主流程（全链路已激活）。

    Pipeline:
        1. open(file_path, "rb").read() → bytes，生成 run_id (UUID前8位)
        2. ext = file_path.split(".")[-1] → 选择 Loader
        3. raw_blocks, doc_metadata = await loader.load(content)
        4. PipelineSnapshot().save("after_load", ...)
        5. ensure_all_tables()  ← 建 knowledge_embeddings + pipeline_jobs 表
        6. run_full_pipeline():
           ├─ chunker.chunk(raw_blocks) → chunks
           ├─ PipelineSnapshot().save("after_chunk", ...)
           ├─ embedder.embed_batch(texts) → embeddings
           ├─ PipelineSnapshot().save("after_embed", ...)
           ├─ store.insert_many(chunks, embeddings, ...)
           └─ store.search(test_emb) → 检索验证
        7. 审计日志 (JSON, event=ingest_complete)
    """

async def run_full_pipeline(
    raw_blocks, run_id, file_path, kb_id, doc_metadata, ext, start_time
) -> None:
    """下游流水线：切块 → 向量化 → 入库 → 检索验证"""
```

**CLI 参数**：

| 参数 | 短写 | 默认值 | 说明 |
|------|------|--------|------|
| `--file` | `-f` | **必填** | 文档路径 |
| `--kb_id` | `-k` | `"default"` | 知识库 ID |
| `--allow_caption_fallback` | — | `False` | 图片描述失败时降级为 alt 文本，不中断 |

**Loader 路由表**（`ingest_knowledge.py:79-94`）：

| 扩展名 | Loader 类 | 特有参数 |
|--------|----------|---------|
| `pdf` | `PDFLoader(enable_ocr, ocr_lang, ocr_chars_threshold, ocr_image_ratio_threshold, column_gap_ratio, min_words_per_column, y_tolerance)` | OCR 配置 + 分栏检测参数 |
| `docx` | `DocxLoader()` | — |
| `xlsx` | `ExcelLoader()` | — |
| `md` | `MarkdownLoader(enable_caption=True, fail_on_caption_error=..., base_dir=doc_dir)` | 图片描述 + 文档目录 |
| `txt` | 同上 | 同上 |

**run_id 机制**：每次执行生成 `uuid.uuid4()[:8]`，作为审计日志的唯一标识。入口日志会打印 `📄 处理文件` 和 `=== RUN END: {run_id} ===`。

**审计日志**：Pipeline 完成时输出一行 JSON 日志（logger name="audit"），包含 `event`, `run_id`, `file`, `kb_id`, `chunks`, `elapsed`, `status` 字段。

---

### 5.2 日志与数据库初始化

#### `src/logger.py` ★ — 统一日志模块

```python
def get_logger(name: str) -> logging.Logger:
    """
    获取已配置好的日志记录器。
    
    特性:
    - 控制台 Handler: INFO 级别
    - 文件 Handler: DEBUG 级别, RotatingFileHandler (10MB, 5备份)
    - 格式: 时间 - logger名 - 级别 - 文件名:行号 - 函数名 - 消息
    - 自动创建 logs/ 目录
    - 防重复添加 Handler（clear + 重建）
    """
```

日志文件位于 `rag-ingest-gateway/logs/rag_ingest.log`，自动轮转。

#### `src/db_init.py` ★ — 数据库初始化

```python
async def ensure_all_tables():
    """应用启动/脚本运行前，统一初始化所有数据库表"""
    # 1. 创建 knowledge_embeddings 表（含 IVF 索引）
    # 2. 创建 pipeline_jobs 任务审计表
```

在 `ingest_knowledge.py` 中于 Loader 产出后调用，避免 Loader 失败时浪费建表资源。

---

### 5.3 Loader 层（数据输入）

#### `loaders/base.py` ★ — 抽象基类

```python
class DocumentLoader(ABC):
    @abstractmethod
    async def load(self, content: bytes) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        将二进制内容解析为 (标准化块列表, 文档级元数据)。
        每个块格式：{"page_num": int, "text": str, "metadata": dict}
        """
```

**所有 Loader 遵守此契约**。PDF/DOCX/XLSX 分别返回各自的 blocks 和 doc_metadata。

#### `loaders/pdf_loader.py` ★★ — 生产级 PDF 解析器

```python
class PDFLoader(DocumentLoader):
    def __init__(
        self,
        enable_ocr: bool = True,
        ocr_lang: str = "chi_sim+eng",
        ocr_chars_threshold: int = 15,
        ocr_image_ratio_threshold: float = 0.3,
        column_gap_ratio: float = 0.1,
        min_words_per_column: int = 5,
        y_tolerance: float = 3.0,
    ):
        """
        核心能力:
        - 基于 pdfplumber 的列感知文本提取
        - 表格自动识别 + Markdown 格式输出
        - 标题启发式注入 (## 标记)
        - 扫描件智能判定 + OCR 降级 (pdf2image + pytesseract)
        - 异步线程池包装同步阻塞操作
        """
    
    async def load(self, content: bytes) -> Tuple[List[Dict], Dict]:
        """
        解析流程:
        1. pdfplumber.open → 逐页处理
        2. _extract_mixed_flow() → 表格+文本混合排序提取
        3. _clean_and_format_text() → 清洗 + 标题注入
        4. 扫描件判定 (_compute_image_area_ratio + ocr_chars_threshold)
        5. OCR 降级 (如启用)
        
        每个 block 的 metadata 含: tables_count, is_scanned, has_images
        doc_metadata 含: title, author, creator, producer, total_pages
        """
```

**PDF 表格识别**：使用严格的 `lines` 策略（只认显式框线），杜绝"空文本格"假表。提取后自动转为 Markdown 表格格式。

**PDF 标题注入**：启发式正则匹配以下模式并注入 `## ` 前缀：
- `1. 基础单栏文本测试`
- `一、概述`
- `[左栏：向量检索与 Embedding 维度]`

**扫描件判定逻辑**：
1. 页面文本字符数 < `ocr_chars_threshold`（默认15）
2. 图像面积占比 ≥ `ocr_image_ratio_threshold`（默认0.3）
3. 或：完全无文本但有物理图片 → 触发 OCR

#### `loaders/docx_loader.py` ★

```python
class DocxLoader(DocumentLoader):
    async def load(self, content: bytes) -> tuple[List[Dict], Dict]:
        """python-docx 提取段落 → 检测 Heading 样式 → 无段落时解析表格"""
```

#### `loaders/excel_loader.py` ★

```python
class ExcelLoader(DocumentLoader):
    async def load(self, content: bytes) -> tuple[List[Dict], Dict]:
        """pandas.read_excel → 逐行转为 "列名:值" 键值对 → 保留 Sheet 名"""
```

#### `loaders/markdown_loader.py` ★★ — 最复杂的 Loader

```python
class MarkdownLoader(DocumentLoader):
    def __init__(
        self,
        enable_caption: bool = True,
        fail_on_caption_error: bool = True,
        base_dir: str = "",
    ):
        """
        Args:
            enable_caption: 是否启用 Qwen-VL 图片描述
            fail_on_caption_error: True=失败抛异常中断, False=降级用 alt 文本
            base_dir: 文档所在目录，用于将 ./images/x.jpg 解析为绝对路径
        """
    
    async def load(self, content: bytes) -> tuple[List[Dict], Dict]:
        """
        完整流程:
        1. decode("utf-8")
        2. MarkdownCleaner.clean(text) → 清洗 + 提取图片
        3. 提取第一个 # 标题 → doc_title
        4. 遍历 image_paths → ImageCaptioner.describe_image()
        5. 段落分割 (双换行)
        6. 替换 [IMG_N] 占位符为实际描述文本
        7. 构造 blocks（含 is_heading, is_code, title 等 metadata）
        8. 关闭 DashScope HTTP 连接池
        """
```

**Block 数据结构**（Loader 统一产出）：

```python
{
    "page_num": 1,            # 页码（PDF 保留实际页码，其余为 1）
    "text": "# 标题\n内容...", # 清洗后的文本
    "metadata": {
        "block_index": 0,     # 块序号
        "is_heading": True,   # 是否为 Markdown 标题
        "is_code": False,     # 是否包含 <CODE_BLOCK>
        "char_count": 42,     # 字符数
        "contains_images": [],# 关联的图片索引
        "title": "文档标题",   # ★ 文档级标题（每个块都带，供向量库溯源）
    }
}
```

---

### 5.4 Cleaner 层（清洗）

#### `cleaners/markdown_cleaner.py` ★★ — 4 层清洗管道

```python
class MarkdownCleaner:
    async def clean(self, text: str) -> tuple[str, Dict[str, Any]]:
        """
        输入: 原始 Markdown 文本
        输出: (清洗后文本, 清洗元数据)
        
        管道:
        Layer 1: 去 Frontmatter (---...---) + HTML 注释 + 提取图片→[IMG_N] + 去裸URL
        Layer 2: 标题 # 后加空格 + LaTeX 包裹
        Layer 3: 删版权声明 + 删 TODO 占位符
        Layer 4: ``` 代码块 → <CODE_BLOCK>...</CODE_BLOCK>
        收尾: 合并多余空行
        
        clean_metadata 包含: image_paths, stripped_frontmatter_count 等
        """
```

> **注意**：`format_cleaner.py` / `structure_normalizer.py` / `rule_filter.py` / `code_block_marker.py` 是独立实现的 Cleaner 子类，继承自 `Cleaner` 抽象基类，但**未被实际 Pipeline 调用**。当前使用的清洗逻辑全部集中在 `MarkdownCleaner` 中。

---

### 5.5 Vision 层（AI 增强）

#### `vision/image_captioner.py` ★★ — 图片描述

```python
class ImageCaptioner:
    CAPTION_PROMPT = """你是一个高精度的文档与图像解析专家..."""
    
    def __init__(self, fail_on_error: bool = True):
        """从环境变量读取 API Key + 模型名，设置 dashscope.api_key"""
    
    def resolve_image_path(self, image_url: str, alt: str = "") -> str:
        """将相对路径/本地路径解析为可用的绝对路径或保持 HTTP URL"""
    
    async def describe_image(self, image_url: str, alt: str = "") -> str:
        """
        调用 DashScope Qwen-VL (AioMultiModalConversation) 描述图片内容。
        
        支持三种图片传入方式:
        - http/https URL → 服务端直接下载
        - 本地绝对路径 → SDK 自动上传 OSS
        - 本地相对路径 → resolve_image_path() 补全为绝对路径
        
        Returns: 自然语言描述文本（如 "[自然照片] 画面展示了..."）
        Raises: RuntimeError（fail_on_error=True 时）或返回降级占位符
        """
    
    @staticmethod
    async def close_session():
        """关闭 DashScope 异步 HTTP 连接池 (close_shared_aio_session)"""
```

**使用的模型**：`qwen3.7-plus`（从 `.env` 的 `ALI_MODEL_NAME` 读取，默认 `qwen-vl-plus`）。

**API Key 优先级**：`DASHSCOPE_API_KEY` > `ALI_API_KEY`。

**提示词策略**：针对 RAG 优化的自适应提示词，自动识别图片类型（表格/图表/流程图/文档截图/自然照片）并针对性提取。

---

### 5.6 Chunking 层（切分）

#### `chunking/semantic_chunker.py` ★

```python
class SemanticChunker:
    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 50):
        """RecursiveCharacterTextSplitter，中文友好"""
    
    def chunk(self, blocks: List[Dict]) -> List[Dict]:
        """
        输入: List[{"page_num","text","metadata"}]
        输出: List[{"content","metadata"}]    ← 注意字段名 text → content!
        
        短文本 (< chunk_size * 0.5 = 256字) 不切分，原样保留。
        """
```

**分隔符优先级**：`["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]`

---

### 5.7 Embedding 层（向量化）

#### `embedding/embedder.py` ★

```python
class OllamaEmbedder:
    def __init__(self, base_url: str, model: str = "nomic-embed-text"):
        """base_url 默认 http://localhost:11434"""
    
    async def embed_text(self, text: str) -> List[float]:
        """单条嵌入 → POST /api/embeddings → 768 维"""
    
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量嵌入（串行调用，过滤空文本，失败用零向量占位 [0.0]*768）"""
```

---

### 5.8 Storage 层（持久化）

#### `storage/vector_store.py` ★

```python
class VectorStore:
    def __init__(self):
        """从 settings.POSTGRES_DSN 读取连接串"""
    
    async def _get_connection(self):
        """
        获取异步数据库连接（async with 上下文管理器）。
        自动 CREATE EXTENSION vector + register_vector_async。
        """
    
    async def ensure_table(self):
        """建 knowledge_embeddings 表 + IVF 索引（vector_cosine_ops, lists=10）"""
    
    async def ensure_pipeline_jobs_table(self):
        """
        建 pipeline_jobs 任务状态表。
        Schema: id(UUID), kb_id, source_file, file_sha256, status,
                risk_flags(JSONB), estimated_tokens, error_message,
                snapshot_path, created_at, updated_at
        含 idx_pipeline_jobs_sha256 + idx_pipeline_jobs_status 索引。
        """
    
    async def insert_many(
        self, chunks: List[Dict], embeddings: List[List[float]],
        source_file: str, kb_id: str = "default"
    ):
        """逐条 INSERT（防御性建表 + 维度校验 !=768 则跳过）"""
    
    async def search(
        self, query_vector: List[float], kb_id: str = None, top_k: int = 3
    ) -> List[Dict]:
        """cosine 相似度检索 → [{content, source_file, kb_id, similarity}]"""
```

**写入前校验**：`len(emb) != 768` → 跳过该条。

---

### 5.9 Snapshot（可观测）

#### `snapshot.py` ★★ — Pipeline Snapshot

```python
class PipelineSnapshot:
    def __init__(self, base_dir: Optional[Path] = None):
        """默认 base_dir = phase1/snapshots/"""
    
    def save(
        self,
        stage: str,              # "after_load" | "after_chunk" | "after_embed"
        source_file: str,        # 原始文档路径
        blocks: List[Dict],      # Loader 或 Chunker 产出的块列表
        doc_metadata: Dict = None,
        elapsed: float = 0.0,
        ext: str = "",
    ):
        """
        写入统一快照（JSON + TXT）。
        
        Loader 产出:  {"page_num", "text", "metadata"}
        Chunker 产出: {"content", "metadata"}   ← _normalize_blocks() 自动适配
        
        JSON Schema: {pipeline, document, images, blocks, stats}
        TXT: 人类可读预览（[H] 标题 / [CODE] 代码块 / [Table] 表格 / [Sheet:xxx] 工作表）
        """
```

**快照 JSON 结构**：

```json
{
  "pipeline": {"version": "1.0", "stage": "after_load", "timestamp": "...", "elapsed_seconds": 2.3},
  "document": {"source_file": "...", "format": "md", "title": "...", "image_count": 2, "block_count": 7},
  "images": [{"alt": "...", "url": "...", "index": 0, "caption": "..."}],
  "blocks": [{"index": 0, "page_num": 1, "text": "...", "metadata": {...}}],
  "stats": {"total_chars": 1489, "avg_chars_per_block": 212.7, "code_blocks": 0, "heading_blocks": 2}
}
```

---

## 6. 请求生命周期

### 6.1 完整调用链路（Markdown 文件含图片）

```
CLI: python scripts/ingest_knowledge.py -f data/test_with_images.md -k default --allow_caption_fallback
  │
  ▼
main(file_path="data/test_with_images.md", kb_id="default", fail_on_caption_error=False)
  │
  ├─[1] run_id = uuid.uuid4()[:8]   # 生成执行ID
  ├─[2] content = open("data/test_with_images.md", "rb").read()
  │     输出: bytes
  │
  ├─[3] ext="md" → loader = MarkdownLoader(enable_caption=True, fail_on_caption_error=False, base_dir="data")
  │
  ├─[4] raw_blocks, doc_metadata = await loader.load(content)
  │     │
  │     ├─[4a] text = content.decode("utf-8")
  │     ├─[4b] cleaned_text, clean_metadata = await cleaner.clean(text)
  │     │       Layer1: 去 Frontmatter / HTML 注释 / 提取图片 → [IMG_0], [IMG_1]
  │     │       Layer2: 标题规范化
  │     │       Layer3: 去版权 / TODO
  │     │       Layer4: ``` → <CODE_BLOCK>
  │     │       输出: clean_metadata["image_paths"] = [...]
  │     │
  │     ├─[4c] title = re.search(r"^# (.+)$", cleaned_text) → doc_title
  │     │
  │     ├─[4d] for img in image_paths:
  │     │       img_url = resolve_image_path(Path(base_dir) / url)
  │     │       caption = await captioner.describe_image(img_url, img["alt"])
  │     │         │
  │     │         │  DashScope AioMultiModalConversation.call(
  │     │         │    model="qwen3.7-plus",
  │     │         │    messages=[{"role":"user","content":[
  │     │         │      {"image": "/abs/path/to/team.jpg"},  ← SDK 自动上传 OSS
  │     │         │      {"text": "你是一个高精度的文档..."}
  │     │         │    ]}]
  │     │         │  )
  │     │       img["caption"] = caption
  │     │     captioner.close_session()  ← close_shared_aio_session()
  │     │
  │     ├─[4e] paragraphs = re.split(r'\n\s*\n', cleaned_text)
  │     └─[4f] 构造 blocks → (List[Block], clean_metadata)
  │
  ├─[5] PipelineSnapshot().save("after_load", ...)
  │     输出: snapshots/after_load/test_with_images_YYYYMMDD_HHMMSS.json + .txt
  │
  ├─[6] await ensure_all_tables()
  │     建 knowledge_embeddings 表 + pipeline_jobs 任务表
  │
  ├─[7] run_full_pipeline():
  │     │
  │     ├─[7a] chunker.chunk(raw_blocks)
  │     │     输入: N blocks → 输出: M chunks (M ≥ N)
  │     │
  │     ├─[7b] PipelineSnapshot().save("after_chunk", ...)
  │     │
  │     ├─[7c] embedder.embed_batch(texts)
  │     │     输入: M 条 text → 输出: M × [768维向量]
  │     │
  │     ├─[7d] PipelineSnapshot().save("after_embed", ...)
  │     │     仅存储文本切片，不存嵌入向量（向量过大）
  │     │
  │     ├─[7e] store.insert_many(chunks, embeddings, file_path, kb_id)
  │     │     输出: knowledge_embeddings 表中新增 M 行
  │     │
  │     └─[7f] store.search(test_emb, kb_id=kb_id, top_k=2)
  │           检索验证：用第一条 chunk 的前50字符做测试查询
  │
  └─[8] 审计日志: audit_logger.info(json.dumps({event, run_id, ...}))
```

### 6.2 PDF 文件路径

```
CLI: python scripts/ingest_knowledge.py -f data/sample.pdf

main()
  ├─ loader = PDFLoader(
  │     enable_ocr=True, ocr_lang="chi_sim+eng",
  │     ocr_chars_threshold=15, ocr_image_ratio_threshold=0.3,
  │     column_gap_ratio=0.1, min_words_per_column=5, y_tolerance=3.0
  │   )
  ├─ raw_blocks, doc_metadata = await loader.load(content)
  │     ├─ pdfplumber.open → 逐页处理
  │     ├─ _extract_mixed_flow() → 表格+文本混合排序
  │     ├─ _clean_and_format_text() → 清洗 + 标题 ## 注入
  │     ├─ 扫描件判定 → OCR 降级（如启用且文本不足）
  │     └─ return (blocks, {title, author, total_pages, ...})
  │
  ├─ PipelineSnapshot().save("after_load", ...)
  ├─ ensure_all_tables()
  └─ run_full_pipeline() → 切块+嵌入+入库+验证
```

### 6.3 非 Markdown/PDF 文件路径（Word/Excel）

```
CLI: python scripts/ingest_knowledge.py -f data/sample.docx

main()
  ├─ loader = DocxLoader()
  ├─ raw_blocks, doc_metadata = await loader.load(content)
  │     python-docx 段落提取 → 空段落按表格降级
  │     （无额外清洗、无图片描述）
  │
  ├─ PipelineSnapshot().save("after_load", ...)  ← 同样执行
  └─ ... 后续相同
```

---

## 7. 上游接入方式

### 7.1 Tool 模式（推荐先跑通）

上游对话助手直接导入 Phase1 入口函数：

```python
import sys
sys.path.insert(0, "/home/ubhuazhu/dev_pros/rag-ingest-gateway/phase1/src")
sys.path.insert(0, "/home/ubhuazhu/dev_pros/rag-ingest-gateway/phase1/scripts")

from ingest_knowledge import main as ingest_main

# 对话助手中调用
await ingest_main(
    file_path="/tmp/uploaded_doc.pdf",
    kb_id="user_123",
    fail_on_caption_error=False,
)
```

### 7.2 关键注意事项

| 事项 | 说明 |
|------|------|
| **虚拟环境** | Phase1 依赖需与对话助手在同一虚拟环境（或确保 `dashscope`/`psycopg`/`pgvector`/`pdfplumber` 等已安装） |
| **sys.path** | 需将 `phase1/src` 和 `phase1/scripts` 加入路径 |
| **base_dir** | `ingest_knowledge.py:72` 自动从 `file_path` 提取父目录并传入 `MarkdownLoader` |
| **HITL 门禁** | Pipeline Snapshot 的 `stats` 字段天然适合做入库前的确认决策 |
| **日志输出** | 日志同时写入控制台和 `logs/rag_ingest.log`，审计日志通过 `audit` logger 输出 |
| **OCR 前置条件** | PDF OCR 需要系统安装 `tesseract` + `poppler-utils`，并 `pip install pdf2image pytesseract` |

---

## 8. 配置参考

### .env 示例

```env
# PostgreSQL
POSTGRES_USER=langgraph_user
POSTGRES_PASSWORD=xxx
POSTGRES_DB=langgraph_db
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# Ollama
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
OLLAMA_BASE_URL=http://localhost:11434
EMBEDDING_DIM=768

# Chunking
CHUNK_SIZE=512
CHUNK_OVERLAP=50

# 阿里云百炼 DashScope（图片描述）
ALI_MODEL_NAME=qwen3.7-plus
ALI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALI_API_KEY=sk-xxx

# PDF 解析
PDF_ENABLE_OCR=True
PDF_OCR_LANG=chi_sim+eng
PDF_OCR_CHARS_THRESHOLD=15
PDF_OCR_IMAGE_RATIO_THRESHOLD=0.3
PDF_COLUMN_GAP_RATIO=0.1
PDF_MIN_WORDS_PER_COLUMN=5
PDF_Y_TOLERANCE=3.0
```

### Settings 类（`src/config.py`）

```python
from config import settings

settings.POSTGRES_DSN                    # ★ 自动拼接的 PostgreSQL 连接串 (computed_field)
settings.POSTGRES_HOST                   # localhost
settings.POSTGRES_PORT                   # 5432
settings.OLLAMA_BASE_URL                 # Ollama 地址
settings.EMBEDDING_MODEL                 # 嵌入模型名 (alias OLLAMA_EMBEDDING_MODEL)
settings.EMBEDDING_DIM                   # 向量维度 (768)
settings.CHUNK_SIZE                      # 切块最大字符数 (512)
settings.CHUNK_OVERLAP                   # 切块重叠字符数 (50)
settings.TOP_K                           # 检索返回数 (3)

# PDF 解析参数
settings.PDF_ENABLE_OCR                  # OCR 开关 (True/False)
settings.PDF_OCR_LANG                    # OCR 语言 ("chi_sim+eng")
settings.PDF_OCR_CHARS_THRESHOLD         # 触发 OCR 的字符数阈值 (15)
settings.PDF_OCR_IMAGE_RATIO_THRESHOLD   # 触发 OCR 的图像面积占比阈值 (0.3)
settings.PDF_COLUMN_GAP_RATIO            # 分栏检测的间距比例阈值 (0.1)
settings.PDF_MIN_WORDS_PER_COLUMN        # 分栏最少单词数 (5)
settings.PDF_Y_TOLERANCE                 # 同行聚类的垂直容差 (3.0)
```

> **Config 技术细节**：使用 `pydantic-settings` 的 `BaseSettings`，`case_sensitive=True`，`.env` 路径为 `phase1/.env`，`extra="ignore"` 忽略未定义变量。

---

## 9. 基础设施依赖

| 服务 | 用途 | Phase1 当前状态 |
|------|------|:--:|
| **DashScope (阿里云百炼)** | Qwen-VL 图片描述 | ✅ 已接入 |
| **Ollama** | nomic-embed-text 文本嵌入（768维） | ✅ 已接入 |
| **PostgreSQL + pgvector** | 向量存储 + 相似度检索 | ✅ 已接入 |
| **Tesseract + Poppler** | PDF 扫描件 OCR（可选） | ⬜ 可选依赖 |

### 数据库表

#### knowledge_embeddings（VectorStore 自动建表）

```sql
CREATE TABLE knowledge_embeddings (
    id          SERIAL PRIMARY KEY,
    title       TEXT,
    content     TEXT NOT NULL,
    embedding   vector(768),
    source_file TEXT,
    kb_id       VARCHAR(50) DEFAULT 'default',
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_kb_id ON knowledge_embeddings(kb_id);
CREATE INDEX idx_knowledge_embedding ON knowledge_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
```

#### pipeline_jobs（任务审计表，VectorStore 自动建表）

```sql
CREATE TABLE pipeline_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id           VARCHAR(50) NOT NULL DEFAULT 'default',
    source_file     TEXT NOT NULL,
    file_sha256     TEXT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    risk_flags      JSONB DEFAULT '[]',
    estimated_tokens INT,
    error_message   TEXT,
    snapshot_path   TEXT,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_pipeline_jobs_sha256 ON pipeline_jobs(file_sha256);
CREATE INDEX idx_pipeline_jobs_status ON pipeline_jobs(status);
```

> **pipeline_jobs 用途**：支持审计门禁、HITL 审批、大文件异步任务管理。当前已建表，业务写入逻辑待后续接入。

---

## 10. 编码规范摘要

| 规则 | 要求 |
|------|------|
| 语言 | Python 3.11+ |
| 异步 | `async/await` 全链路（Loader/Cleaner/Embedder/Store 全异步；PDF 同步操作通过 `asyncio.to_thread` 包装） |
| 类型注解 | 关键函数有（非强制完整覆盖） |
| 配置 | 从 `config.py` → `.env` 读取（pydantic-settings），禁止硬编码 |
| 导入 | `sys.path.insert` 注入 `src/` 后绝对导入 |
| 清洗管道 | 仅 Markdown 触发；PDF 有自带的 `_clean_and_format_text` 清洗逻辑 |
| 错误处理 | EAFP（try/except）+ `fail_on_caption_error` 开关控制 |
| 图片路径 | 相对路径在 `ImageCaptioner.resolve_image_path()` 中解析为绝对路径 |
| 日志 | 统一使用 `get_logger(__name__)`，不要直接用 `print()` 或 `logging.getLogger()` |
| PDF 表格策略 | 使用 `lines` 策略，只认显式框线，杜绝空文本格假表 |

---

## 11. 快速检查清单

- [ ] **环境激活**：确保 `.env` 中 API Key 已配置
- [ ] **依赖安装**：`pip install -r requirements.txt`（含 pdfplumber, dashscope, aiohttp, langchain-text-splitters, httpx, psycopg, pgvector, pydantic-settings 等）
- [ ] **OCR 依赖**（可选）：`sudo apt install tesseract-ocr poppler-utils && pip install pdf2image pytesseract`
- [ ] **服务运行**：PostgreSQL + pgvector 运行中，Ollama 运行中
- [ ] **测试 Markdown 清洗**：`python scripts/ingest_knowledge.py -f data/sample.md --allow_caption_fallback`
- [ ] **测试 PDF 解析**：`python scripts/ingest_knowledge.py -f data/sample.pdf`
- [ ] **测试图片描述**：需要有网络可达的图片 URL 或本地图片 + DashScope API Key
- [ ] **检查快照**：`ls snapshots/after_load/` — 应有 `.json` + `.txt` 两个文件（三阶段各一份）
- [ ] **检查日志**：`tail -f logs/rag_ingest.log`
- [ ] **理解入口**：`scripts/ingest_knowledge.py` → `main()` → `run_full_pipeline()`
- [ ] **理解 Loader 基类**：`src/loaders/base.py` → `load()` 返回 `tuple[List, Dict]`
- [ ] **理解 PDF 解析**：`src/loaders/pdf_loader.py` → 列感知 + 表格 + OCR 完整链路
- [ ] **理解清洗管道**：`src/cleaners/markdown_cleaner.py` → `clean()` 4 层处理
- [ ] **理解图片调度**：`src/loaders/markdown_loader.py` → 图片循环 + `close_session()`
- [ ] **理解快照 Schema**：`src/snapshot.py` → `save()` + `_normalize_blocks()` + `_write_readable()`
- [ ] **理解日志系统**：`src/logger.py` → `get_logger()` + RotatingFileHandler
- [ ] **理解数据库**：`src/storage/vector_store.py` → `ensure_table()` + `ensure_pipeline_jobs_table()`

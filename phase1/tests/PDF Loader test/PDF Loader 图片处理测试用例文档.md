# PDF Loader 图片处理测试用例文档

## 测试环境

| 项目         | 值                                                           |
| :----------- | :----------------------------------------------------------- |
| 项目路径     | `/home/ubhuazhu/dev_pros/rag-ingest-gateway`                 |
| 测试命令     | `poetry run python phase1/scripts/ingest_knowledge.py --file <测试文件> --kb_id <测试ID> --allow_caption_fallback` |
| 预期产物目录 | `phase1/debug_cleaned/`、`phase1/snapshots/`                 |


## 测试用例清单

| 编号      | 测试用例                  | 测试内容                                           | 输入                              | 预期结果                                                     |
| :-------- | :------------------------ | :------------------------------------------------- | :-------------------------------- | :----------------------------------------------------------- |
| **TC-01** | 基础 PDF 解析             | Docling 能否正常解析 PDF 并导出 Markdown           | `test_rag_spec.pdf`               | `debug_cleaned/pdf_clean_{hash}.md` 生成，内容完整           |
| **TC-02** | 页眉页脚过滤              | 页眉页脚是否被正确移除                             | `test_rag_spec.pdf`               | Markdown 中不包含页码（如 "P04"）或页眉文本                  |
| **TC-03** | 双栏布局重排              | 双栏内容是否按物理阅读顺序排列                     | `test_rag_spec.pdf`               | 左栏“向量检索”先出现，右栏“LLM上下文”随后                    |
| **TC-04** | 表格位置保持              | 表格是否在正文1和正文2之间，而非堆到底部           | `test_rag_spec.pdf`               | 正文1 → 表格 → 正文2 顺序正确                                |
| **TC-05** | HTML表格 → Markdown Table | `<table>` 标签是否被转换为标准 Markdown 表格       | `test_rag_spec.pdf`               | 表格以 `\| --- \|` 格式呈现，无 `<table>` 标签               |
| **TC-06** | **本地图片导出**          | 内嵌图片是否导出为独立文件                         | 含图片的 PDF                      | `debug_cleaned/images/image_*.png` 文件存在                  |
| **TC-07** | **本地图片引用**          | Markdown 中图片路径是否为绝对路径                  | 含图片的 PDF                      | `![图片](/abs/path/.../images/image_*.png)`                  |
| **TC-08** | **网络图片下载**          | 网络图片是否下载到本地                             | 含网络图片引用的 PDF              | `debug_cleaned/web_images/web_*.png` 文件存在                |
| **TC-09** | **网络图片引用替换**      | Markdown 中 URL 是否被替换为本地路径               | 含网络图片引用的 PDF              | 无 `https://` 图片链接，全为本地绝对路径                     |
| **TC-10** | **统一 IR 管道**          | PDF 转换后的 Markdown 是否经过 MarkdownLoader 处理 | `test_rag_spec.pdf`               | 日志出现 `🔄 PDF 已转为 Markdown，进入统一清洗与图片描述管道...` |
| **TC-11** | **图片描述生成**          | ImageCaptioner 是否为图片生成描述                  | 含图片的 PDF + DashScope API 可用 | 日志出现 `🖼️ 图片: X 张，已描述: Y 张`，且 Y > 0              |
| **TC-12** | 端到端入库                | 完整流程能否入库成功                               | `test_rag_spec.pdf`               | 数据库 `knowledge_embeddings` 中存在 `kb_id = 'test_*'` 记录 |
| **TC-13** | 检索验证                  | 入库后能否检索到内容                               | `test_rag_spec.pdf`               | 控制台输出 `✅ 检索测试通过，返回 N 条结果`                   |


## 测试执行记录表

### 基本信息

| 项目         | 值                                     |
| :----------- | :------------------------------------- |
| 执行日期     | `____年__月__日`                       |
| 测试人员     | ______________                         |
| 测试环境     | `poetry env info` 输出：______________ |
| Docling 版本 | `poetry show docling`：______________  |

---

#### TC-01：基础 PDF 解析

| 项目         | 内容                                                         |
| :----------- | :----------------------------------------------------------- |
| **测试文件** | `phase1/data/test_rag_spec.pdf`                              |
| **执行命令** | `poetry run python phase1/scripts/ingest_knowledge.py --file phase1/data/test_rag_spec.pdf --kb_id test_basic --allow_caption_fallback` |
| **预期结果** | `debug_cleaned/pdf_clean_*.md` 文件生成                      |
| **实际结果** | ✅ / ❌                                                        |
| **备注**     |                                                              |

---

#### TC-02：页眉页脚过滤

| 项目         | 内容                                                         |
| :----------- | :----------------------------------------------------------- |
| **检查方法** | `cat phase1/debug_cleaned/pdf_clean_*.md | grep -i "页码\|P0\d"` |
| **预期结果** | 无匹配行（"P04" 被过滤）                                     |
| **实际结果** | ✅ / ❌                                                        |
| **备注**     |                                                              |

---

#### TC-03：双栏布局重排

| 项目         | 内容                                                         |
| :----------- | :----------------------------------------------------------- |
| **检查方法** | `cat phase1/debug_cleaned/pdf_clean_*.md | grep -A2 -B2 "向量检索"` |
| **预期结果** | "向量检索" 在 "LLM上下文窗口" 之前                           |
| **实际结果** | ✅ / ❌                                                        |
| **备注**     |                                                              |

---

#### TC-04：表格位置保持

| 项目         | 内容                                                         |
| :----------- | :----------------------------------------------------------- |
| **检查方法** | `cat phase1/debug_cleaned/pdf_clean_*.md | grep -E "正文|PostgreSQL\|Milvus\|Elasticsearch" -C3` |
| **预期结果** | 正文1 → 表格（含 PostgreSQL/Milvus） → 正文2                 |
| **实际结果** | ✅ / ❌                                                        |
| **备注**     |                                                              |

---

#### TC-05：HTML表格 → Markdown Table

| 项目         | 内容                                                         |
| :----------- | :----------------------------------------------------------- |
| **检查方法** | `cat phase1/debug_cleaned/pdf_clean_*.md | grep -E "\|---\||<table>"` |
| **预期结果** | 存在 `\| --- \|` 分隔线，不存在 `<table>` 标签               |
| **实际结果** | ✅ / ❌                                                        |
| **备注**     |                                                              |

---

#### TC-06：本地图片导出

| 项目         | 内容                                  |
| :----------- | :------------------------------------ |
| **检查方法** | `ls -la phase1/debug_cleaned/images/` |
| **预期结果** | 存在 `.png` 或 `.jpg` 图片文件        |
| **实际结果** | ✅ / ❌                                 |
| **备注**     |                                       |

---

#### TC-07：本地图片引用

| 项目         | 内容                                                         |
| :----------- | :----------------------------------------------------------- |
| **检查方法** | `cat phase1/debug_cleaned/pdf_clean_*.md | grep -E '!\[.*\]\(.*/images/'` |
| **预期结果** | 图片引用为绝对路径 `(/home/.../images/image_*.png)`          |
| **实际结果** | ✅ / ❌                                                        |
| **备注**     |                                                              |

---

#### TC-08：网络图片下载（需准备含网络图片的 PDF）

| 项目         | 内容                                                         |
| :----------- | :----------------------------------------------------------- |
| **准备方法** | 用 Python 生成：`poetry run python -c "from reportlab.pdfgen import canvas; c = canvas.Canvas('phase1/data/test_web.pdf'); c.drawString(100, 750, '网络图片测试'); c.save()"`（需要手动插入网络图片链接） |
| **检查方法** | `ls -la phase1/debug_cleaned/web_images/`                    |
| **预期结果** | `web_*.png` 或 `web_*.jpg` 文件存在                          |
| **实际结果** | ✅ / ❌                                                        |
| **备注**     | 如果 PDF 中无网络图片引用，此项可跳过                        |

---

#### TC-09：网络图片引用替换（同上，需含网络图片的 PDF）

| 项目         | 内容                                                         |
| :----------- | :----------------------------------------------------------- |
| **检查方法** | `cat phase1/debug_cleaned/pdf_clean_*.md | grep -E '!\[.*\]\(https?://'` |
| **预期结果** | 无输出（所有网络 URL 已被替换为本地路径）                    |
| **实际结果** | ✅ / ❌                                                        |
| **备注**     |                                                              |

---

#### TC-10：统一 IR 管道

| 项目         | 内容                                                         |
| :----------- | :----------------------------------------------------------- |
| **检查方法** | 查看控制台日志，搜索 `🔄 PDF 已转为 Markdown，进入统一清洗与图片描述管道...` |
| **预期结果** | 该日志出现，且之后有 `📖 正在解析文档...`（第二次）           |
| **实际结果** | ✅ / ❌                                                        |
| **备注**     |                                                              |

---

#### TC-11：图片描述生成

| 项目         | 内容                                                   |
| :----------- | :----------------------------------------------------- |
| **检查方法** | 查看控制台日志，搜索 `🖼️ 图片: X 张，已描述: Y 张`      |
| **预期结果** | Y > 0（至少有一张图片被成功描述）                      |
| **实际结果** | ✅ / ❌                                                  |
| **备注**     | 如果 PDF 中无图片或 DashScope API 不可用，此项可能失败 |

---

#### TC-12：端到端入库

| 项目         | 内容                                                         |
| :----------- | :----------------------------------------------------------- |
| **检查方法** | `psql -d langgraph_db -c "SELECT COUNT(*) FROM knowledge_embeddings WHERE kb_id = 'test_basic';"` |
| **预期结果** | 返回行数 > 0（向量已入库）                                   |
| **实际结果** | ✅ / ❌                                                        |
| **备注**     |                                                              |

---

#### TC-13：检索验证

| 项目         | 内容                                                 |
| :----------- | :--------------------------------------------------- |
| **检查方法** | 查看控制台日志，搜索 `✅ 检索测试通过，返回 N 条结果` |
| **预期结果** | N >= 1（至少有一条检索结果）                         |
| **实际结果** | ✅ / ❌                                                |
| **备注**     |                                                      |


## 测试结论

| 编号  | 测试用例                  | 结果 (✅/❌) | 备注 |
| :---- | :------------------------ | :--------- | :--- |
| TC-01 | 基础 PDF 解析             |            |      |
| TC-02 | 页眉页脚过滤              |            |      |
| TC-03 | 双栏布局重排              |            |      |
| TC-04 | 表格位置保持              |            |      |
| TC-05 | HTML表格 → Markdown Table |            |      |
| TC-06 | 本地图片导出              |            |      |
| TC-07 | 本地图片引用              |            |      |
| TC-08 | 网络图片下载              |            |      |
| TC-09 | 网络图片引用替换          |            |      |
| TC-10 | 统一 IR 管道              |            |      |
| TC-11 | 图片描述生成              |            |      |
| TC-12 | 端到端入库                |            |      |
| TC-13 | 检索验证                  |            |      |

**总体结论**：通过 ___ / 失败 ___ / 部分通过 ___


## 准备测试 PDF 的脚本

如果手边没有合适的测试 PDF，可以用以下脚本生成：

```bash
# 创建包含内嵌图片的测试 PDF（使用 Pillow + reportlab）
poetry run python -c "
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw
import os

# 1. 先创建一个简单的测试图片
img_path = 'phase1/data/test_image.png'
img = Image.new('RGB', (200, 150), color='lightblue')
d = ImageDraw.Draw(img)
d.text((20, 60), '测试图片', fill='darkblue')
img.save(img_path)

# 2. 创建 PDF 并嵌入图片
c = canvas.Canvas('phase1/data/test_with_image.pdf', pagesize=A4)
c.drawString(100, 800, 'PDF 包含内嵌图片测试')
c.drawString(100, 770, '以下是嵌入的图片：')
c.drawImage(ImageReader(img_path), 100, 550, width=200, height=150)
c.save()
print('✅ 测试 PDF 已创建: phase1/data/test_with_image.pdf')
"
```

## 测试通过标准（最低要求）

| 优先级       | 必须通过的用例                                         |
| :----------- | :----------------------------------------------------- |
| **核心功能** | TC-01, TC-02, TC-03, TC-04, TC-05, TC-10, TC-12, TC-13 |
| **图片处理** | TC-06, TC-07                                           |
| **网络图片** | TC-08, TC-09（可选，视是否有网络图片而定）             |
| **图片描述** | TC-11（如果 DashScope API 可用）                       |
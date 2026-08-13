# /rag_data_dispose/phase1/src/vision/image_captioner.py
import aiohttp
import os
from typing import Optional
import dashscope
from http import HTTPStatus
from config import settings
from pathlib import Path


class ImageCaptioner:
    # 针对 RAG 优化的自适应提示词（保持不变）
    CAPTION_PROMPT = """你是一个高精度的文档与图像解析专家。请仔细分析这张图片，并根据其具体类型进行最利于 RAG（检索增强生成）系统检索的结构化解析：

1. **识别图片类型**：首先在回答的最开始用一行标明图片属于哪种类型（例如：[数据表格] / [数据图表] / [流程图/逻辑图] / [文档截图/PPT] / [自然照片] 等）。
2. **根据类型提取核心信息**：
   - **若是【数据表格（Table）】**：请忽略任何视觉装饰或颜色，直接高精度提取并使用 **标准 Markdown 表格格式** 输出表格中的所有行与列数据，确保数值、表头完全精确且不遗漏关键指标。
   - **若是【数据图表（Chart，如折线/柱状/饼图等）】**：指明图表类型、横纵坐标的含义、数值单位、关键趋势、极值（最大/最小值）或图表得出的核心结论。
   - **若是【流程图/关系图/架构图（Diagram）】**：梳理图中的逻辑流向、各个节点名称、上下游连接关系，以清晰的 Markdown 分级列表还原图中的业务或技术架构。
   - **若是【文档/PPT 截图（Document）】**：对图片中包含的重要文本、标题进行高精度的转录（OCR），尽可能保留段落与列表格式。
   - **若是【自然照片/常规图像（Photo）】**：描述画面中的核心主体（人/物/场景/核心动作）、布局方位、颜色基调以及图片传达的核心信息。
3. **一句话总结**：在解析的最后，提供一段 1-2 句的简明总结，提炼出这张图片想要表达的核心主题或中心思想，以便于语义向量进行高层次检索。

请确保输出结构清晰、无客套废话，直接输出解析后的内容。"""

    def __init__(self, fail_on_error: bool = True):
        """
        Args:
            fail_on_error: True=图片描述失败时抛出异常，False=降级使用alt文本
        """
        # 1. 获取 API Key（兼容原有环境变量）
        api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("ALI_API_KEY")
        # 2. 设置 DashScope 全局 API Key（原生客户端使用）
        dashscope.api_key = api_key

        # 3. 模型名称（保持可配置）
        self.model = os.getenv("ALI_MODEL_NAME") or "qwen-vl-plus"
        self.fail_on_error = fail_on_error

        # 可选：如果需要自定义端点（一般无需修改）
        # base_url = os.getenv("ALI_BASE_URL")
        # if base_url:
        #     dashscope.base_http_url = base_url

    def resolve_image_path(self, image_url: str, alt: str = "") -> str:
        # 如果已经是HTTP/HTTPS链接，直接返回
        if image_url.startswith(('http://', 'https://')):
            return image_url
        
        # 如果是本地路径，转换为绝对路径
        if os.path.exists(image_url):
            return str(Path(image_url).resolve())
        
        # 如果路径不存在，尝试基于当前工作目录补全
        # 这里可以根据你的文档路径进行更智能的处理
        return image_url

    async def describe_image(self, image_url: str, alt: str = "") -> str:
        """
        调用 Qwen-VL 描述图片内容
        支持传入本地文件路径（绝对/相对）、file:// 协议或 HTTP(S) URL，
        原生 SDK 会自动识别并处理（本地文件会上传至 DashScope 临时存储）。

        Raises:
            Exception: 当 fail_on_error=True 且调用失败时抛出
            RuntimeError: 当 fail_on_error=False 且调用失败时抛出

        args:
            image_url: 图片路径或URL
            alt: 图片的alt文本（用于降级描述）
        """
        # 预处理：确保路径有效
        image_url = self.resolve_image_path(image_url, alt=alt)
        

        try:
            # 使用 DashScope 原生异步接口
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"image": image_url},          # 直接传入路径/URL，自动处理
                        {"text": self.CAPTION_PROMPT},
                    ],
                }
            ]
            # 在 DashScope（通义千问）原生 Python SDK 中，异步类的命名遵循的是 Aio（基于 aiohttp）前缀规范
            # ，而不是标准的 Async。因此，在多模态（VL/语音/视频）模型的异步调用中，对应的正确类名是 AioMultiModalConversation。
            async with aiohttp.ClientSession() as session:
                response = await dashscope.AioMultiModalConversation.call(
                    model=self.model,
                    messages=messages,
                    session=session  # 传入会话
                )

            # 检查响应状态
            if response.status_code == HTTPStatus.OK:
                # 提取返回的文本内容
                return response.output.choices[0].message.content[0]["text"]
            else:
                # API 返回错误状态
                raise Exception(f"API 返回错误: {response.code} - {response.message}")

        except Exception as e:
            if self.fail_on_error:
                # 快速失败：抛出包含上下文信息的异常
                raise RuntimeError(f"图片描述生成失败 [alt: {alt}, url: {image_url}]: {e}")
            else:
                # 降级模式：返回占位符
                print(f"⚠️ 图片描述生成失败: {e}")
                return f"[图片: {alt}]" if alt else f"[图片无法描述: {image_url}]"

    @staticmethod
    async def close_session():
        """关闭 DashScope 异步 HTTP 连接池，消除 Unclosed client session 警告"""
        await dashscope.close_shared_aio_session()
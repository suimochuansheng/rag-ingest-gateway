# src/db_init.py
from storage.vector_store import VectorStore


async def ensure_all_tables():
    """应用启动/脚本运行前，统一初始化所有数据库表"""
    # 1. 确保向量表存在
    store = VectorStore()
    # 表中自带防御性建表逻辑，确保表存在，无需业务逻辑添加判断
    await store.ensure_table()  # 建向量表
    await store.ensure_pipeline_jobs_table()  # 建任务表

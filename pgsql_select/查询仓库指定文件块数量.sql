-- # 1. 确认测试 PDF 已入库
SELECT COUNT(*) FROM knowledge_embeddings WHERE source_file LIKE '%test_rag_spec.pdf%';

-- # 2. 确认结果（应该返回一个大于 0 的数字）
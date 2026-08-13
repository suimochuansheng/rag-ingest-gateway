-- #!/bin/bash
-- set -e
-- echo "🗄️ 清理数据库..."
-- psql -U langgraph_user -d langgraph_db -h localhost <<'SQL'
-- TRUNCATE TABLE knowledge_embeddings RESTART IDENTITY CASCADE;
-- TRUNCATE TABLE knowledge_embeddings_bgem3 RESTART IDENTITY CASCADE;
-- TRUNCATE TABLE pipeline_jobs RESTART IDENTITY CASCADE;
-- TRUNCATE TABLE task_states RESTART IDENTITY CASCADE;
-- DO $$
-- DECLARE
--     tbl TEXT;
-- BEGIN
--     FOR tbl IN
--         SELECT tablename FROM pg_tables
--         WHERE schemaname='public'
--           AND tablename LIKE 'checkpoint%'
--     LOOP
--         EXECUTE 'TRUNCATE TABLE ' || quote_ident(tbl) || ' CASCADE';
--     END LOOP;
-- END $$;
-- SQL
-- echo "✅ 清理完成！"

TRUNCATE TABLE knowledge_embeddings RESTART IDENTITY CASCADE;
TRUNCATE TABLE knowledge_embeddings_bgem3 RESTART IDENTITY CASCADE;
TRUNCATE TABLE pipeline_jobs RESTART IDENTITY CASCADE;
TRUNCATE TABLE task_states RESTART IDENTITY CASCADE;

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT tablename FROM pg_tables
        WHERE schemaname='public'
          AND tablename LIKE 'checkpoint%'
    LOOP
        EXECUTE 'TRUNCATE TABLE ' || quote_ident(tbl) || ' CASCADE';
    END LOOP;
END $$;
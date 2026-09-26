-- Local development bootstrap. Runs once, on first container start.
--
-- IMPORTANT: this file only affects a LOCALLY created Docker volume. On Supabase
-- the extensions below are already available and the migration creates them
-- itself (see alembic/versions/..._initial_schema.py), so this script is not
-- part of the production path.

-- Slow-query visibility (blueprint v3 §17.3).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Required by the initial migration:
--   pg_trgm  - trigram index so partial-word search works ("deduc" ->
--              "Deductions"). Full-text search alone matches whole lexemes and
--              would return nothing for a partial word.
--   pgcrypto - gen_random_bytes / digest helpers.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- DEFERRED, not forgotten: pgvector.
-- The superseded blueprint used pgvector for AI semantic search and duplicate
-- doubt detection. Under the v3 stack, AI is a Phase 3 item and PostgreSQL FTS
-- is the Phase 1-2 answer, so the extension is not created here. When the AI
-- phase is scheduled, the trigger for adding it is the same scale trigger that
-- governs the search decision - do not add a vector store before then.
--
-- CREATE EXTENSION IF NOT EXISTS vector;

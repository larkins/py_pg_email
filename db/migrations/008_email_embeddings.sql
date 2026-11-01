-- =============================================================================
-- Migration 008: Embedding columns on `emails` + the indexes search needs
-- =============================================================================
--
-- WHY THIS EXISTS
-- ---------------
-- The search service and the embedding worker in this repository read and
-- write three columns on `emails`:
--
--   subject_embedding    halfvec(1024)  the subject's embedding, cosine
--                                     searched through an HNSW index
--   subject_normalized   varchar(500)   the subject, normalised for the
--                                     trigram/keyword search
--   references_chain     text           the In-Reply-To/References chain
--                                     the threading resolver walks
--
-- Migration 003 created the `email_chunks` table and its `halfvec(1024)`
-- embedding column, and the `vector` and `pg_trgm` extensions, but it did
-- not add these three to `emails`. So they existed only on the databases
-- that had them applied by hand -- a fresh install, and the CI database,
-- were both built WITHOUT them.
--
-- The consequence was not cosmetic: with the column missing, every query
-- in app/services/search_service.py failed with
--
--     psycopg2.errors.UndefinedColumn: column e.subject_embedding does not exist
--
-- which is why the search tests could never pass on a database built from
-- this repository's own schema.
--
-- The definitions below are the ones the production database already has,
-- so applying this migration to a database that was set up by hand is a
-- no-op and applying it to a fresh database brings it into line.
--
-- Every statement is idempotent. The indexes are named exactly as they are
-- in production, so `IF NOT EXISTS` matches the existing ones rather than
-- building a second copy.
--
--   subject_embedding    halfvec(1024)
--     The dimension is 1024, not the model's native 2560: halfvec(1024)
--     fits inside a Postgres page, 2560 does not. The embedding service is
--     asked for 1024 dimensions to match.
-- =============================================================================

-- The extensions the indexes below need. Migration 003 creates them too,
-- but this migration must stand on its own.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- The columns
-- ---------------------------------------------------------------------------
ALTER TABLE emails
    ADD COLUMN IF NOT EXISTS subject_embedding halfvec(1024);

ALTER TABLE emails
    ADD COLUMN IF NOT EXISTS subject_normalized VARCHAR(500);

ALTER TABLE emails
    ADD COLUMN IF NOT EXISTS references_chain TEXT;

-- ---------------------------------------------------------------------------
-- The indexes
-- ---------------------------------------------------------------------------

-- Cosine similarity over the subject embeddings.
CREATE INDEX IF NOT EXISTS emails_subject_embedding_hnsw
    ON emails USING hnsw (subject_embedding halfvec_cosine_ops);

-- Keyword search over the subject, and over the references chain.
CREATE INDEX IF NOT EXISTS emails_subject_trgm_idx
    ON emails USING gin (subject gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_emails_references_trgm
    ON emails USING gin (references_chain gin_trgm_ops);

-- The normalised subject is only set on the mails that have one, so the
-- index is partial.
CREATE INDEX IF NOT EXISTS idx_emails_subject_normalized
    ON emails USING btree (subject_normalized)
    WHERE subject_normalized IS NOT NULL;

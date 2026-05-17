-- FinAdvisor Database Schema
-- PostgreSQL 16 + pgvector
-- Run: psql -f schema.sql

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- DOCUMENTS TABLE — source documents with metadata
-- ============================================================
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT NOT NULL,
    doc_type        TEXT NOT NULL CHECK (doc_type IN (
                        'product_factsheet',
                        'suitability_rule',
                        'compliance_memo',
                        'jurisdiction_disclosure'
                    )),
    jurisdiction    TEXT NOT NULL CHECK (jurisdiction IN ('US', 'EU', 'UK', 'APAC')),
    tier_required   INT NOT NULL DEFAULT 1 CHECK (tier_required BETWEEN 1 AND 4),
    regulatory_ref  TEXT,
    last_reviewed_at DATE NOT NULL,
    product_category TEXT,
    risk_level      TEXT CHECK (risk_level IN ('conservative', 'moderate', 'aggressive')),
    raw_content     TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- CHUNKS TABLE — embedded chunks with RLS
-- ============================================================
CREATE TABLE chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INT NOT NULL,
    content         TEXT NOT NULL,
    embedding       vector(1024) NOT NULL,
    jurisdiction    TEXT NOT NULL,
    tier_required   INT NOT NULL,
    regulatory_ref  TEXT,
    last_reviewed_at DATE NOT NULL,
    source_title    TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX idx_chunks_embedding ON chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

CREATE INDEX idx_chunks_document_id ON chunks (document_id);
CREATE INDEX idx_chunks_jurisdiction ON chunks (jurisdiction);
CREATE INDEX idx_chunks_tier ON chunks (tier_required);

-- ============================================================
-- ROW-LEVEL SECURITY
-- ============================================================
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;

CREATE POLICY chunk_visibility ON chunks
    FOR SELECT
    USING (
        tier_required <= current_setting('app.user_tier')::int
        AND jurisdiction = ANY(
            string_to_array(current_setting('app.user_jurisdictions'), ',')
        )
    );

-- ============================================================
-- ROLES
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'finadvisor_service') THEN
        CREATE ROLE finadvisor_service;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'finadvisor_app') THEN
        CREATE ROLE finadvisor_app;
    END IF;
END
$$;

GRANT SELECT ON chunks TO finadvisor_app;
GRANT ALL ON chunks TO finadvisor_service;
GRANT ALL ON documents TO finadvisor_service;
GRANT SELECT ON documents TO finadvisor_app;

-- ============================================================
-- SUITABILITY RULES TABLE
-- ============================================================
CREATE TABLE suitability_rules (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_name           TEXT NOT NULL,
    product_category    TEXT NOT NULL,
    client_risk_profile TEXT NOT NULL CHECK (client_risk_profile IN (
                            'conservative', 'moderate', 'aggressive'
                        )),
    min_tier_required   INT NOT NULL DEFAULT 1,
    jurisdiction        TEXT NOT NULL,
    regulatory_ref      TEXT NOT NULL,
    rule_text           TEXT NOT NULL,
    last_reviewed_at    DATE NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT now()
);

GRANT ALL ON suitability_rules TO finadvisor_service;
GRANT SELECT ON suitability_rules TO finadvisor_app;

-- ContextBridge schema v2: attachments (screenshots, PDFs, documents) stored with a project.
-- Files are copied into <data dir>/attachments/<slug>/<sha256>.<ext> (mode 600); the DB keeps metadata.
-- Text-like files are also indexed for search (attachments_fts, rowid = attachments.id).

CREATE TABLE attachments (
  id           INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id   INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  entry_id     INTEGER REFERENCES entries(id) ON DELETE SET NULL,
  filename     TEXT NOT NULL,
  ext          TEXT NOT NULL,
  mime         TEXT NOT NULL,
  bytes        INTEGER NOT NULL,
  sha256       TEXT NOT NULL,
  note         TEXT,
  source_path  TEXT,
  provenance   TEXT NOT NULL CHECK (provenance IN ('user_stated','ai_suggested','user_confirmed','verified','imported')),
  created_at   TEXT NOT NULL,
  UNIQUE (project_id, sha256)
);
CREATE INDEX attachments_project ON attachments(project_id, created_at);

CREATE VIRTUAL TABLE attachments_fts USING fts5(filename, note, text, tokenize='porter unicode61');

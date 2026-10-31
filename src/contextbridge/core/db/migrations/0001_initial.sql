-- ContextBridge schema v1 (docs/BLUEPRINT.md §7.2).
-- Applied inside one transaction by core/db/migrate.py after a pre-migration backup.
-- Timestamps are ISO-8601 strings with offset; epoch ints only where the source is epoch.

CREATE TABLE projects (
  id                INTEGER PRIMARY KEY,
  slug              TEXT NOT NULL UNIQUE CHECK (slug GLOB '[a-z0-9]*' AND length(slug) BETWEEN 2 AND 63),
  name              TEXT NOT NULL,
  objective         TEXT,
  problem           TEXT,
  target_users      TEXT,
  scope             TEXT,
  success_criteria  TEXT,
  linked_dir        TEXT,
  knowledge_version INTEGER NOT NULL DEFAULT 0,
  state_version     INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  archived_at       TEXT
);

CREATE TABLE global_memory (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('preference','convention','fact')),
  topic_key   TEXT NOT NULL,
  title       TEXT NOT NULL,
  body        TEXT NOT NULL DEFAULT '',
  provenance  TEXT NOT NULL CHECK (provenance IN ('user_stated','ai_suggested','user_confirmed','verified','imported')),
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE sessions (
  id             INTEGER PRIMARY KEY,
  project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  label          TEXT,
  client         TEXT,
  account_label  TEXT,
  external_key   TEXT,
  started_at     TEXT NOT NULL,
  ended_at       TEXT,
  summary        TEXT,
  where_stopped  TEXT,
  next_action    TEXT,
  capture        TEXT NOT NULL DEFAULT 'none' CHECK (capture IN ('checkpoints','transcript','both','none'))
);
CREATE INDEX sessions_project ON sessions(project_id, started_at);
CREATE INDEX sessions_external ON sessions(external_key);

CREATE TABLE entries (
  id                INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id        INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  kind              TEXT NOT NULL CHECK (kind IN ('requirement','decision','rejected_approach','constraint',
                      'preference','fact','explanation','bug','fix','lesson','milestone','risk',
                      'open_question','reference','convention')),
  topic_key         TEXT NOT NULL,
  title             TEXT NOT NULL,
  body              TEXT NOT NULL DEFAULT '',
  rationale         TEXT,
  alternatives      TEXT,
  status            TEXT NOT NULL CHECK (status IN ('active','superseded','rejected','resolved','outdated','unverified')),
  provenance        TEXT NOT NULL CHECK (provenance IN ('user_stated','ai_suggested','user_confirmed','verified','imported')),
  confidence        TEXT NOT NULL CHECK (confidence IN ('confirmed','assumed','uncertain')),
  importance        TEXT NOT NULL CHECK (importance IN ('critical','important','optional')),
  supersedes_id     INTEGER REFERENCES entries(id) ON DELETE SET NULL,
  superseded_by_id  INTEGER REFERENCES entries(id) ON DELETE SET NULL,
  source_ref        TEXT,
  norm_hash         TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  last_verified_at  TEXT,
  version           INTEGER NOT NULL DEFAULT 1,
  CHECK (kind NOT IN ('decision','rejected_approach') OR (rationale IS NOT NULL AND length(trim(rationale)) > 0)),
  CHECK (NOT (provenance = 'ai_suggested' AND kind = 'decision' AND status = 'active'))
);
CREATE INDEX entries_topic ON entries(project_id, kind, topic_key, status);
CREATE INDEX entries_hash ON entries(project_id, norm_hash);

CREATE TABLE entry_history (
  id            INTEGER PRIMARY KEY,
  entry_id      INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  version       INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL,
  change_type   TEXT NOT NULL CHECK (change_type IN ('create','update','merge','supersede','confirm','resolve','restore','delete')),
  changed_at    TEXT NOT NULL,
  session_id    INTEGER
);
CREATE INDEX entry_history_entry ON entry_history(entry_id, version);

CREATE TABLE conflicts (
  id           INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  topic_key    TEXT NOT NULL,
  kind         TEXT NOT NULL,
  entry_a      INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  entry_b      INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
  resolution   TEXT CHECK (resolution IN ('a_supersedes_b','b_supersedes_a','both_valid','merge')),
  note         TEXT,
  detected_at  TEXT NOT NULL,
  resolved_at  TEXT
);
CREATE INDEX conflicts_open ON conflicts(project_id, status);

CREATE TABLE live_state (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  section     TEXT NOT NULL CHECK (section IN ('architecture','stack','implemented','in_progress','broken',
                'needs_testing','postponed','blockers','dependencies','outstanding_decisions','environment','repo')),
  content     TEXT NOT NULL,
  refs        TEXT,
  version     INTEGER NOT NULL DEFAULT 1,
  updated_at  TEXT NOT NULL,
  UNIQUE (project_id, section)
);

CREATE TABLE live_state_history (
  id             INTEGER PRIMARY KEY,
  live_state_id  INTEGER NOT NULL REFERENCES live_state(id) ON DELETE CASCADE,
  version        INTEGER NOT NULL,
  content        TEXT NOT NULL,
  refs           TEXT,
  changed_at     TEXT NOT NULL,
  session_id     INTEGER
);

CREATE TABLE tasks (
  id                INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title             TEXT NOT NULL,
  status            TEXT NOT NULL CHECK (status IN ('todo','in_progress','blocked','done','postponed')),
  priority          INTEGER,
  depends_on        TEXT,
  expected_outcome  TEXT,
  notes             TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE INDEX tasks_project ON tasks(project_id, status);

CREATE TABLE task_history (
  id             INTEGER PRIMARY KEY,
  task_id        INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  snapshot_json  TEXT NOT NULL,
  changed_at     TEXT NOT NULL,
  session_id     INTEGER
);

CREATE TABLE checkpoints (
  id             INTEGER PRIMARY KEY,
  session_id     INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  summary        TEXT NOT NULL,
  where_stopped  TEXT NOT NULL,
  next_action    TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('manual','auto','emergency','handoff')),
  created_at     TEXT NOT NULL
);
CREATE INDEX checkpoints_project ON checkpoints(project_id, created_at);

CREATE TABLE snapshots (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  component     TEXT NOT NULL CHECK (component IN ('knowledge','state','handoff')),
  version       INTEGER NOT NULL,
  label         TEXT,
  reason        TEXT,
  content_hash  TEXT NOT NULL,
  codec         TEXT NOT NULL DEFAULT 'zstd' CHECK (codec IN ('zstd','gzip','none')),
  payload       BLOB NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX snapshots_project ON snapshots(project_id, component, version);

CREATE TABLE raw_archive (
  id           INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id   INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  source_type  TEXT NOT NULL,
  title        TEXT,
  source_path  TEXT,
  sha256       TEXT NOT NULL,
  codec        TEXT NOT NULL DEFAULT 'zstd' CHECK (codec IN ('zstd','gzip','none')),
  content      BLOB NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE staging (
  id               INTEGER PRIMARY KEY,
  project_id       INTEGER REFERENCES projects(id) ON DELETE CASCADE,
  source_type      TEXT NOT NULL,
  items_json       TEXT NOT NULL,
  redactions_json  TEXT,
  created_at       TEXT NOT NULL,
  expires_at       TEXT NOT NULL
);

CREATE TABLE file_refs (
  id                   INTEGER PRIMARY KEY,
  project_id           INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path                 TEXT NOT NULL,
  purpose              TEXT,
  included_in_exports  INTEGER NOT NULL DEFAULT 0,
  required             INTEGER NOT NULL DEFAULT 0,
  last_seen_commit     TEXT,
  UNIQUE (project_id, path)
);

CREATE TABLE telemetry (
  id                     INTEGER PRIMARY KEY,
  session_key            TEXT NOT NULL,
  project_id             INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  ts                     TEXT NOT NULL,
  source                 TEXT NOT NULL,
  context_used_pct       REAL,
  context_used_tokens    INTEGER,
  context_window_tokens  INTEGER,
  five_hour_pct          REAL,
  five_hour_reset_at     INTEGER,
  seven_day_pct          REAL,
  seven_day_reset_at     INTEGER
);
CREATE INDEX telemetry_session ON telemetry(session_key, ts);

CREATE TABLE handoff_runs (
  id                     INTEGER PRIMARY KEY,
  project_id             INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  mode                   TEXT NOT NULL,
  profile                TEXT NOT NULL,
  estimated_tokens_low   INTEGER,
  estimated_tokens_high  INTEGER,
  actual_chars           INTEGER,
  bytes                  INTEGER,
  file_count             INTEGER,
  validation_score       REAL,
  created_at             TEXT NOT NULL
);

-- Full-text search over knowledge entries (external content, synced by triggers).
CREATE VIRTUAL TABLE entries_fts USING fts5(
  title, body, rationale, content='entries', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
  INSERT INTO entries_fts(rowid, title, body, rationale) VALUES (new.id, new.title, new.body, new.rationale);
END;
CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
  INSERT INTO entries_fts(entries_fts, rowid, title, body, rationale) VALUES ('delete', old.id, old.title, old.body, old.rationale);
END;
CREATE TRIGGER entries_au AFTER UPDATE OF title, body, rationale ON entries BEGIN
  INSERT INTO entries_fts(entries_fts, rowid, title, body, rationale) VALUES ('delete', old.id, old.title, old.body, old.rationale);
  INSERT INTO entries_fts(rowid, title, body, rationale) VALUES (new.id, new.title, new.body, new.rationale);
END;

-- Archive text is compressed in raw_archive; the decompressed text is indexed here by application code.
CREATE VIRTUAL TABLE archive_fts USING fts5(title, text, tokenize='porter unicode61');

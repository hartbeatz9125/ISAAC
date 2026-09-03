CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('explicit','preference','project','personal_fact','conversation_summary','system_isaac','temporary')),
    content TEXT NOT NULL,
    normalized_content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    importance REAL NOT NULL CHECK (importance BETWEEN 0 AND 1),
    last_accessed TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('active','superseded','deleted','expired')),
    tags_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags_json)),
    related_project TEXT,
    supersedes_id TEXT REFERENCES memories(id),
    deletion_reason TEXT
);

CREATE TABLE memory_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL REFERENCES memories(id),
    revision_number INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    changed_at TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    UNIQUE(memory_id, revision_number)
);

CREATE TABLE memory_embeddings (
    memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX memories_status_type_idx ON memories(status, type);
CREATE INDEX memories_expiry_idx ON memories(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX memories_project_idx ON memories(related_project) WHERE related_project IS NOT NULL;

CREATE VIRTUAL TABLE memory_fts USING fts5(
    content,
    tags,
    related_project,
    content='memories',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memory_fts(rowid, content, tags, related_project)
    VALUES (new.rowid, new.content, new.tags_json, coalesce(new.related_project, ''));
END;

CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, content, tags, related_project)
    VALUES ('delete', old.rowid, old.content, old.tags_json, coalesce(old.related_project, ''));
END;

CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, content, tags, related_project)
    VALUES ('delete', old.rowid, old.content, old.tags_json, coalesce(old.related_project, ''));
    INSERT INTO memory_fts(rowid, content, tags, related_project)
    VALUES (new.rowid, new.content, new.tags_json, coalesce(new.related_project, ''));
END;

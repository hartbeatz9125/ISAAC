CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    UNIQUE(conversation_id, sequence)
);

CREATE INDEX IF NOT EXISTS conversations_last_active_idx
ON conversations(last_active_at DESC);

CREATE INDEX IF NOT EXISTS conversation_messages_order_idx
ON conversation_messages(conversation_id, sequence);

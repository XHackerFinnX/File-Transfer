-- SQLite migration, applied automatically and transactionally by ApexEmailQueue.initialize.
CREATE TABLE IF NOT EXISTS apex_email_meta (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS apex_email_queue (
    message_id TEXT PRIMARY KEY,
    body_sha256 TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('registration', 'password_reset')),
    recipient_tag TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    received_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'retry', 'sending', 'sent', 'expired', 'cancelled', 'failed')),
    encrypted_body BLOB,
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0,
    lease_until REAL NOT NULL DEFAULT 0,
    lease_token TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    completed_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS apex_email_due ON apex_email_queue(status, retry_at, lease_until);
CREATE INDEX IF NOT EXISTS apex_email_recipient ON apex_email_queue(recipient_tag, kind, created_at);
INSERT OR IGNORE INTO apex_email_meta(name, value) VALUES ('schema_version', '1');

"""Inspect/backup an existing order queue. Never sends mail or connects to PostgreSQL."""

import argparse
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def open_existing(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError()
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def queue_status(path):
    path = Path(path).resolve()
    with open_existing(path) as conn:
        conn.execute("BEGIN")
        meta_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='webhook_outbox_meta'").fetchone()
        meta = conn.execute("SELECT value FROM webhook_outbox_meta WHERE name='queue_id'").fetchone() if meta_exists else None
        counts = {row["status"]: row["n"] for row in conn.execute(
            "SELECT status,count(*) AS n FROM webhook_outbox GROUP BY status")}
        rows = conn.execute("""
            SELECT id,target,site,status,attempts,last_error,retry_at,lease_until
            FROM webhook_outbox WHERE status<>'done' ORDER BY id LIMIT 100
        """).fetchall()
        completed = conn.execute("""
            SELECT id,target,site,attempts,completed_at FROM webhook_outbox
            WHERE status='done' ORDER BY completed_at DESC LIMIT 20
        """).fetchall()
        return {"ok": True, "queue_path": str(path), "queue_id": meta[0] if meta else None,
                "counts": counts, "pending": [dict(row) for row in rows],
                "recent_completed": [dict(row) for row in completed]}


def backup_queue(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output:
        raise FileExistsError()
    with open_existing(source) as conn:
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        try:
            destination = sqlite3.connect(output)
            try:
                conn.backup(destination, pages=256, sleep=0.05)
            finally:
                destination.close()
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    result = queue_status(output)
    return {"ok": True, "backup_path": str(output), "queue_id": result["queue_id"], "counts": result["counts"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "backup"))
    parser.add_argument("--path", help="Existing queue file; defaults to TILDA_QUEUE_PATH")
    parser.add_argument("--output", help="New backup file; must not exist")
    args = parser.parse_args()
    if args.command == "backup" and not args.output:
        parser.error("backup requires --output")
    try:
        path = args.path
        if path is None:
            from config import config
            path = config.TILDA_QUEUE_PATH
        result = queue_status(path) if args.command == "status" else backup_queue(path, args.output)
    except FileNotFoundError:
        result = {"ok": False, "error": "queue_file_missing"}
    except FileExistsError:
        result = {"ok": False, "error": "backup_already_exists_or_is_source"}
    except Exception:
        # Do not print SQL, raw submissions, environment values or connection strings.
        result = {"ok": False, "error": "queue_inspection_or_backup_failed"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

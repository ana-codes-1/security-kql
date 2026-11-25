import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class StateStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "demo.sqlite3"
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS spans (
                    trace_id TEXT, span_id TEXT PRIMARY KEY, parent_id TEXT,
                    name TEXT, start_ns INTEGER, end_ns INTEGER,
                    status TEXT, attributes TEXT
                );
                CREATE INDEX IF NOT EXISTS spans_by_trace ON spans(trace_id, start_ns);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS evaluations (
                    id TEXT PRIMARY KEY, created_at TEXT, payload TEXT
                );
                """
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, json.dumps(value)))

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def save_spans(self, spans):
        with self.connect() as db:
            for span in spans:
                context = span.context
                db.execute(
                    "INSERT OR REPLACE INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"{context.trace_id:032x}",
                        f"{context.span_id:016x}",
                        f"{span.parent.span_id:016x}" if span.parent else None,
                        span.name,
                        span.start_time,
                        span.end_time,
                        span.status.status_code.name,
                        json.dumps(dict(span.attributes or {}), default=str),
                    ),
                )

    def trace(self, trace_id):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM spans WHERE trace_id=? ORDER BY start_ns", (trace_id,)
            ).fetchall()
        return [
            {
                "name": row["name"],
                "span_id": row["span_id"],
                "parent_id": row["parent_id"],
                "duration_ms": round((row["end_ns"] - row["start_ns"]) / 1e6, 2),
                "start_ns": row["start_ns"],
                "status": row["status"],
                "attributes": json.loads(row["attributes"]),
            }
            for row in rows
        ]

    def save_evaluation(self, payload):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO evaluations VALUES (?, ?, ?)",
                (payload["id"], datetime.now(UTC).isoformat(), json.dumps(payload)),
            )

    def evaluation(self, evaluation_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM evaluations WHERE id=?", (evaluation_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

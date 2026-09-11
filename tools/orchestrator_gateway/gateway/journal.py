"""Durable intent/result recording. Never persist source payloads or credentials."""

import hashlib
import hmac
import json
import os
import sqlite3
import time

from .policy import Denied


class Journal:
    def __init__(self, path: str, key: str, write_limit: int):
        self.key = key.encode()
        self.limit = write_limit
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS operations (
              id TEXT PRIMARY KEY, operation TEXT NOT NULL, fingerprint TEXT NOT NULL,
              status TEXT NOT NULL, created REAL NOT NULL, result TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL,
              operation TEXT NOT NULL, outcome TEXT NOT NULL
            );
        """)

    def digest(self, data: bytes) -> str:
        return hmac.new(self.key, data, hashlib.sha256).hexdigest()

    def begin(self, operation: str, key: str, payload: dict) -> tuple[str, dict | None]:
        operation_id = self.digest(key.encode())
        fingerprint = self.digest(
            json.dumps(
                [operation, payload], sort_keys=True, separators=(",", ":")
            ).encode()
        )
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT fingerprint,status,result FROM operations WHERE id=?",
                (operation_id,),
            ).fetchone()
            if row:
                if not hmac.compare_digest(row[0], fingerprint):
                    raise Denied("IDEMPOTENCY_KEY_REUSED")
                if row[1] != "SUCCEEDED":
                    raise Denied("OPERATION_RECONCILIATION_REQUIRED")
                self.db.commit()
                return operation_id, json.loads(row[2])
            count = self.db.execute(
                "SELECT COUNT(*) FROM operations WHERE created>?", (time.time() - 60,)
            ).fetchone()[0]
            if count >= self.limit:
                raise Denied("WRITE_RATE_LIMITED", 429)
            self.db.execute(
                "INSERT INTO operations VALUES(?,?,?,?,?,NULL)",
                (operation_id, operation, fingerprint, "PENDING", time.time()),
            )
            self.db.execute(
                "INSERT INTO events(created,operation,outcome) VALUES(?,?,?)",
                (time.time(), operation, "INTENT"),
            )
            self.db.commit()
            return operation_id, None
        except Exception:
            self.db.rollback()
            raise

    def finish(self, operation_id: str, operation: str, result: dict) -> None:
        # Service returns only mutation metadata; never PR text or file content.
        with self.db:
            self.db.execute(
                "UPDATE operations SET status='SUCCEEDED',result=? WHERE id=?",
                (json.dumps(result), operation_id),
            )
            self.db.execute(
                "INSERT INTO events(created,operation,outcome) VALUES(?,?,?)",
                (time.time(), operation, "SUCCEEDED"),
            )

    def audit(self, operation: str, outcome: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO events(created,operation,outcome) VALUES(?,?,?)",
                (time.time(), operation, outcome),
            )

    def close(self) -> None:
        self.db.close()

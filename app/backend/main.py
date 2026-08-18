"""Demo multi-tenant "notes" API.

Infra-level isolation (namespace/RBAC/NetworkPolicy/ResourceQuota — see infra/namespaces/) is
the point of this project, not application-level multi-tenancy logic, so this app is
deliberately minimal: one stateless FastAPI process per tenant namespace, each with its own
local SQLite file. It exists to be a realistic target for the isolation/fairness scenarios in
scenarios/ — something with real HTTP traffic and CPU/memory usage the monitors can observe.
"""
import os
import socket
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

TENANT_ID = os.environ.get("TENANT_ID", "unknown-tenant")
DB_PATH = os.environ.get("DB_PATH", "/tmp/notes.db")

app = FastAPI(title=f"notes-api ({TENANT_ID})")


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class NoteIn(BaseModel):
    title: str
    body: str


class Note(NoteIn):
    id: int
    created_at: str


@app.on_event("startup")
def on_startup() -> None:
    _init_db()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/whoami")
def whoami() -> dict:
    """Identifies which tenant's instance answered — the scenario/evaluation scripts use this
    to confirm which app instance actually served a request (e.g. after a cross-namespace
    isolation-violation scenario, or to disambiguate load-generator targets)."""
    return {
        "tenant": TENANT_ID,
        "hostname": socket.gethostname(),
        "pod_ip": os.environ.get("POD_IP", ""),
        "node_name": os.environ.get("NODE_NAME", ""),
    }


@app.post("/notes", response_model=Note, status_code=201)
def create_note(note: NoteIn) -> Note:
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, body, created_at) VALUES (?, ?, ?)",
            (note.title, note.body, created_at),
        )
        note_id = cur.lastrowid
    return Note(id=note_id, created_at=created_at, **note.model_dump())


@app.get("/notes", response_model=list[Note])
def list_notes() -> list[Note]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM notes ORDER BY id").fetchall()
    return [Note(**dict(row)) for row in rows]


@app.get("/notes/{note_id}", response_model=Note)
def get_note(note_id: int) -> Note:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    return Note(**dict(row))


@app.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int) -> None:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="note not found")

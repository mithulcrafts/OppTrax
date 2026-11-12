import asyncio
import copy
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import dns.resolver
from motor.motor_asyncio import AsyncIOMotorClient

from config import LOCAL_DB_FALLBACK, LOCAL_DB_PATH, MONGO_URI, USE_LOCAL_DB


def _resolve_field(doc: dict[str, Any], dotted_key: str) -> tuple[Any, bool]:
    cur: Any = doc
    for part in dotted_key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None, False
    return cur, True


def _eval_operator(op: str, val: Any, cur: Any, found: bool, expected: dict[str, Any]) -> bool:
    ops = {
        "$ne": lambda: cur != val,
        "$eq": lambda: cur == val,
        "$in": lambda: any(item in val for item in cur) if isinstance(cur, list) else (cur in val),
        "$nin": lambda: cur not in val,
        "$gte": lambda: cur is not None and cur >= val,
        "$lte": lambda: cur is not None and cur <= val,
        "$gt": lambda: cur is not None and cur > val,
        "$lt": lambda: cur is not None and cur < val,
        "$exists": lambda: bool(val) == found,
    }
    if op in ops:
        return ops[op]()
    if op == "$regex":
        flags = re.IGNORECASE if "i" in str(expected.get("$options", "")) else 0
        return cur is not None and bool(re.search(val, str(cur), flags))
    return True


def _match_condition(doc: dict[str, Any], key: str, expected: Any) -> bool:
    if key == "$or":
        return any(_matches_filter(doc, sub) for sub in expected)
    if key == "$and":
        return all(_matches_filter(doc, sub) for sub in expected)

    cur, found = _resolve_field(doc, key)
    if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
        return all(_eval_operator(op, val, cur, found, expected) for op, val in expected.items())
    if isinstance(cur, list) and not isinstance(expected, list):
        return expected in cur
    return cur == expected


def _matches_filter(doc: dict[str, Any], filt: dict[str, Any] | None) -> bool:
    if not filt:
        return True
    for key, val in filt.items():
        if not _match_condition(doc, key, val):
            return False
    return True


def _set_nested(doc: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    cur = doc
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = copy.deepcopy(value)


def _unset_nested(doc: dict[str, Any], dotted_key: str) -> None:
    parts = dotted_key.split(".")
    cur = doc
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            return
        cur = cur[part]
    cur.pop(parts[-1], None)


def _get_nested(doc: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    parts = dotted_key.split(".")
    cur: Any = doc
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _apply_array_op(updated: dict[str, Any], op: str, k: str, v: Any) -> None:
    arr = _get_nested(updated, k, None)
    items = v["$each"] if isinstance(v, dict) and "$each" in v else [v]
    if op == "$pull":
        if isinstance(arr, list):
            _set_nested(updated, k, [item for item in arr if item not in items])
        return
    base = list(arr) if isinstance(arr, list) else []
    for item in items:
        if op == "$push" or item not in base:
            base.append(copy.deepcopy(item))
    _set_nested(updated, k, base)


def _apply_update(doc: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(doc)
    for op, fields in update.items():
        if op == "$set":
            for k, v in fields.items():
                _set_nested(updated, k, v)
        elif op == "$unset":
            for k in fields:
                _unset_nested(updated, k)
        elif op == "$inc":
            for k, v in fields.items():
                _set_nested(updated, k, (_get_nested(updated, k, 0) or 0) + v)
        elif op in {"$addToSet", "$push", "$pull"}:
            for k, v in fields.items():
                _apply_array_op(updated, op, k, v)
    return updated


class LocalCursor:
    def __init__(self, docs: list[dict[str, Any]]):
        self._docs = docs
        self._sort_spec: list[tuple[str, int]] = []
        self._limit: int | None = None
        self._iter_index = 0

    def sort(self, key_or_list, direction: int = 1):
        if isinstance(key_or_list, list):
            self._sort_spec = key_or_list
        else:
            self._sort_spec = [(key_or_list, direction)]
        return self

    def limit(self, count: int):
        self._limit = count
        return self

    def _materialize(self) -> list[dict[str, Any]]:
        items = copy.deepcopy(self._docs)
        for key, direction in reversed(self._sort_spec):
            items.sort(
                key=lambda d: (
                    _get_nested(d, key) is None,
                    str(_get_nested(d, key, ""))
                    if not isinstance(_get_nested(d, key), (int, float))
                    else _get_nested(d, key),
                ),
                reverse=(direction == -1),
            )
        if self._limit is not None:
            items = items[: self._limit]
        return items

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        items = self._materialize()
        if length is not None and length >= 0:
            items = items[:length]
        return items

    def __aiter__(self):
        self._iter_items = self._materialize()
        self._iter_index = 0
        return self

    async def __anext__(self):
        if self._iter_index >= len(self._iter_items):
            raise StopAsyncIteration
        item = self._iter_items[self._iter_index]
        self._iter_index += 1
        return item


class InsertResult:
    def __init__(self, inserted_id: Any):
        self.inserted_id = inserted_id


class UpdateResult:
    def __init__(self, matched_count: int, modified_count: int, upserted_id: Any = None):
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class DeleteResult:
    def __init__(self, deleted_count: int):
        self.deleted_count = deleted_count


class LocalAsyncCollection:
    """SQLite-backed Motor-compatible async collection for zero-config local & test execution."""

    def __init__(self, conn: sqlite3.Connection, lock: asyncio.Lock, name: str):
        self._conn = conn
        self._lock = lock
        self._name = name

    def _load_all_sync(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT doc_id, data_json FROM documents WHERE collection = ?",
            (self._name,),
        )
        results = []
        for row in cur.fetchall():
            doc = json.loads(row[1])
            results.append(doc)
        return results

    async def find_one(self, filt: dict[str, Any] | None = None) -> dict[str, Any] | None:
        async with self._lock:
            for doc in self._load_all_sync():
                if _matches_filter(doc, filt):
                    return copy.deepcopy(doc)
        return None

    def find(self, filt: dict[str, Any] | None = None) -> LocalCursor:
        matched = [copy.deepcopy(doc) for doc in self._load_all_sync() if _matches_filter(doc, filt)]
        return LocalCursor(matched)

    async def count_documents(self, filt: dict[str, Any] | None = None) -> int:
        async with self._lock:
            return sum(1 for doc in self._load_all_sync() if _matches_filter(doc, filt))

    async def insert_one(self, doc: dict[str, Any]) -> InsertResult:
        async with self._lock:
            payload = copy.deepcopy(doc)
            doc_id = str(payload.get("_id") or uuid.uuid4().hex)
            payload.setdefault("_id", doc_id)
            self._conn.execute(
                "INSERT INTO documents (collection, doc_id, data_json) VALUES (?, ?, ?)",
                (self._name, doc_id, json.dumps(payload, default=str)),
            )
            self._conn.commit()
            return InsertResult(doc_id)

    async def update_one(
        self,
        filt: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
    ) -> UpdateResult:
        async with self._lock:
            for doc in self._load_all_sync():
                if _matches_filter(doc, filt):
                    updated = _apply_update(doc, update)
                    doc_id = str(updated.get("_id"))
                    self._conn.execute(
                        "UPDATE documents SET data_json = ? WHERE collection = ? AND doc_id = ?",
                        (json.dumps(updated, default=str), self._name, doc_id),
                    )
                    self._conn.commit()
                    return UpdateResult(1, 1)

            if upsert:
                base: dict[str, Any] = {}
                for k, v in (filt or {}).items():
                    if not k.startswith("$") and not isinstance(v, dict):
                        _set_nested(base, k, v)
                created = _apply_update(base, update)
                doc_id = str(created.get("_id") or uuid.uuid4().hex)
                created.setdefault("_id", doc_id)
                self._conn.execute(
                    "INSERT INTO documents (collection, doc_id, data_json) VALUES (?, ?, ?)",
                    (self._name, doc_id, json.dumps(created, default=str)),
                )
                self._conn.commit()
                return UpdateResult(0, 0, upserted_id=doc_id)

            return UpdateResult(0, 0)

    async def delete_one(self, filt: dict[str, Any]) -> DeleteResult:
        async with self._lock:
            for doc in self._load_all_sync():
                if _matches_filter(doc, filt):
                    doc_id = str(doc.get("_id"))
                    self._conn.execute(
                        "DELETE FROM documents WHERE collection = ? AND doc_id = ?",
                        (self._name, doc_id),
                    )
                    self._conn.commit()
                    return DeleteResult(1)
            return DeleteResult(0)

    async def delete_many(self, filt: dict[str, Any] | None = None) -> DeleteResult:
        async with self._lock:
            deleted = 0
            for doc in self._load_all_sync():
                if _matches_filter(doc, filt):
                    doc_id = str(doc.get("_id"))
                    self._conn.execute(
                        "DELETE FROM documents WHERE collection = ? AND doc_id = ?",
                        (self._name, doc_id),
                    )
                    deleted += 1
            self._conn.commit()
            return DeleteResult(deleted)


class LocalAsyncDatabase:
    def __init__(self, db_path: str = LOCAL_DB_PATH):
        if db_path != ":memory:":
            parent = Path(db_path).parent
            os.makedirs(parent, exist_ok=True)
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                collection TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                data_json TEXT NOT NULL,
                PRIMARY KEY (collection, doc_id)
            )
            """
        )
        self._conn.commit()
        self._lock = asyncio.Lock()
        self._collections: dict[str, LocalAsyncCollection] = {}

    def __getitem__(self, name: str) -> LocalAsyncCollection:
        if name not in self._collections:
            self._collections[name] = LocalAsyncCollection(self._conn, self._lock, name)
        return self._collections[name]

    def close(self):
        self._conn.close()


class DatabaseProxy:
    def __init__(self):
        self._backend: Any = None
        self.mode: str = "uninitialized"

    def bind(self, backend: Any, mode: str):
        self._backend = backend
        self.mode = mode

    def __getitem__(self, name: str):
        return CollectionProxy(self, name)


class CollectionProxy:
    def __init__(self, db_proxy: DatabaseProxy, name: str):
        self._db_proxy = db_proxy
        self._name = name

    def _col(self):
        return self._db_proxy._backend[self._name]

    def __getattr__(self, item: str):
        return getattr(self._col(), item)


db = DatabaseProxy()
mongo_client: AsyncIOMotorClient | None = None


def _bind_local(db_path: str = LOCAL_DB_PATH):
    local_db = LocalAsyncDatabase(db_path)
    db.bind(local_db, f"sqlite:{db_path}")


def _configure_initial_db():
    global mongo_client
    if MONGO_URI and not USE_LOCAL_DB:
        if "mongodb+srv://" in MONGO_URI:
            dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
            dns.resolver.default_resolver.nameservers = ["8.8.8.8", "1.1.1.1"]
        mongo_client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db.bind(mongo_client["opptrax_db"], "mongodb")
    else:
        _bind_local(LOCAL_DB_PATH)


_configure_initial_db()


async def init_database() -> str:
    """Verifies MongoDB connectivity on startup and falls back cleanly to SQLite if enabled."""
    if db.mode == "mongodb" and mongo_client is not None:
        try:
            await mongo_client.admin.command("ping")
            print("[DB] Connected to MongoDB (opptrax_db).", flush=True)
            return db.mode
        except Exception as exc:
            print(f"[DB] MongoDB ping failed: {exc}", flush=True)
            if LOCAL_DB_FALLBACK:
                print(f"[DB] Falling back to local SQLite store at {LOCAL_DB_PATH}", flush=True)
                _bind_local(LOCAL_DB_PATH)
                return db.mode
            raise
    print(f"[DB] Using local SQLite persistent store ({db.mode}).", flush=True)
    return db.mode


def get_db_backend() -> str:
    """Returns the currently active database backend identifier."""
    return db.mode


def use_local_database(db_path: str = ":memory:") -> LocalAsyncDatabase:
    """Switches active database backend (used for deterministic tests and local dev)."""
    local_db = LocalAsyncDatabase(db_path)
    db.bind(local_db, f"sqlite:{db_path}")
    return local_db


tasks_collection = db["active_tasks"]
intent_log_collection = db["intent_logs"]
users_collection = db["users"]
opportunities_collection = db["opportunities"]
tracked_collection = db["tracked_board"]
otp_verifications_collection = db["otp_verifications"]
whatsapp_messages_collection = db["whatsapp_messages"]
processed_webhooks_collection = db["processed_webhooks"]

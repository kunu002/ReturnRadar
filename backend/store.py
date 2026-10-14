"""Small collection interface: SQLite locally, Firestore in Cloud Run."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .runtime import validate_configuration


def now():
    return datetime.now(timezone.utc).isoformat()


class LocalStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (collection TEXT, id TEXT, data TEXT, PRIMARY KEY(collection,id))")

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    def all(self, collection):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM records WHERE collection=? ORDER BY id", (collection,))]

    def get(self, collection, key):
        with self.connect() as db:
            row = db.execute("SELECT data FROM records WHERE collection=? AND id=?", (collection, key)).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, collection, key, value):
        with self.connect() as db:
            db.execute("INSERT INTO records VALUES (?,?,?) ON CONFLICT(collection,id) DO UPDATE SET data=excluded.data", (collection, key, json.dumps(value)))

    def create(self, collection, key, value):
        with self.connect() as db:
            return db.execute("INSERT OR IGNORE INTO records VALUES (?,?,?)", (collection, key, json.dumps(value))).rowcount == 1

    def claim(self, collection, key, eligible, updates):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM records WHERE collection=? AND id=?", (collection, key)).fetchone()
            value = json.loads(row[0]) if row else None
            if value is None or not eligible(value):
                return None
            value.update(updates)
            db.execute("UPDATE records SET data=? WHERE collection=? AND id=?", (json.dumps(value), collection, key))
            return value


class FirestoreStore:
    def __init__(self):
        from google.cloud import firestore
        self.db = firestore.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"])

    def ref(self, collection, key):
        import hashlib
        return self.db.collection("rr_" + collection).document(hashlib.sha256(key.encode()).hexdigest())

    def all(self, collection):
        return [d.to_dict() for d in self.db.collection("rr_" + collection).stream()]

    def get(self, collection, key):
        doc = self.ref(collection, key).get()
        return doc.to_dict() if doc.exists else None

    def put(self, collection, key, value):
        self.ref(collection, key).set(value)

    def create(self, collection, key, value):
        from google.api_core.exceptions import AlreadyExists
        try:
            self.ref(collection, key).create(value)
            return True
        except AlreadyExists:
            return False

    def claim(self, collection, key, eligible, updates):
        from google.cloud import firestore
        @firestore.transactional
        def apply(transaction):
            ref = self.ref(collection, key)
            doc = ref.get(transaction=transaction)
            if not doc.exists or not eligible(doc.to_dict()):
                return None
            value = {**doc.to_dict(), **updates}
            transaction.set(ref, value)
            return value
        return apply(self.db.transaction())


class WorkspaceStore:
    """A namespace chosen from a server-authorized membership, never a record ID."""
    def __init__(self, root, workspace_id):
        import hashlib
        self.root = root
        self.workspace_id = workspace_id
        self.prefix = "w_" + hashlib.sha256(workspace_id.encode()).hexdigest() + "__"

    def all(self, collection):
        return self.root.all(self.prefix + collection)

    def get(self, collection, key):
        return self.root.get(self.prefix + collection, key)

    def put(self, collection, key, value):
        return self.root.put(self.prefix + collection, key, value)

    def create(self, collection, key, value):
        return self.root.create(self.prefix + collection, key, value)

    def claim(self, collection, key, eligible, updates):
        return self.root.claim(self.prefix + collection, key, eligible, updates)


# Fail before a Cloud Run instance could create ephemeral SQLite storage or demo data.
validate_configuration()
store = FirestoreStore() if os.getenv("STORAGE_BACKEND") == "firestore" else LocalStore(os.getenv("DATABASE_PATH", "data/returnradar.db"))

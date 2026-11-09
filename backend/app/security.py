from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from passlib.context import CryptContext
from sqlalchemy import create_engine, event, text
import jwt

DATA_DIR = Path(os.getenv("QUERYLENS_DATA_DIR", "./data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "querylens.sqlite3"
DATABASE_URL = os.getenv("QUERYLENS_DATABASE_URL") or f"sqlite:///{DB_PATH.as_posix()}"
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
_sqlite = DATABASE_URL.startswith("sqlite:")
_engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if _sqlite else {}, pool_pre_ping=not _sqlite)
if _sqlite:
    @event.listens_for(_engine, "connect")
    def _enable_sqlite_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
JWT_SECRET = os.getenv("QUERYLENS_SECRET_KEY", "local-development-secret-change-this-before-deploy")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class DatabaseSession:
    """Small qmark-compatible transaction wrapper over SQLite or PostgreSQL."""
    def __enter__(self):
        self.connection = _engine.connect()
        self.transaction = self.connection.begin()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.transaction.rollback() if exc_type else self.transaction.commit()
        finally:
            self.connection.close()

    def execute(self, query: str, params=()):
        pieces = query.split("?")
        if len(pieces) > 1:
            if len(params) != len(pieces) - 1:
                raise ValueError("Query parameter count does not match placeholders.")
            rewritten = pieces[0]
            bind = {}
            for index, piece in enumerate(pieces[1:]):
                key = f"p{index}"
                rewritten += f":{key}" + piece
                bind[key] = params[index]
        else:
            rewritten, bind = query, {}
        return self.connection.execute(text(rewritten), bind).mappings()

    def executescript(self, script: str):
        for statement in script.split(";"):
            if statement.strip():
                self.connection.execute(text(statement))


def connect():
    return DatabaseSession()


def initialize():
    with connect() as db:
        migration_name = "0001_initial.sql" if _sqlite else "0001_initial_postgresql.sql"
        migration = Path(__file__).parent.parent / "migrations" / migration_name
        db.executescript(migration.read_text(encoding="utf-8"))


def create_user(email: str, password: str):
    import uuid
    now = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        db.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (str(uuid.uuid4()), email.lower().strip(), pwd_context.hash(password), now))
        return db.execute("SELECT id,email FROM users WHERE email=?", (email.lower().strip(),)).fetchone()


def authenticate(email: str, password: str):
    with connect() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (email.lower().strip(),)).fetchone()
    if not user or not pwd_context.verify(password, user["password_hash"]): return None
    return user


def token_for(user_id: str):
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user_id, "iat": now, "exp": now + timedelta(hours=12)}, JWT_SECRET, algorithm="HS256")


def decode_token(token: str):
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])["sub"]

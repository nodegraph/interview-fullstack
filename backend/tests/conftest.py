import os
from collections.abc import Generator
from urllib.parse import urlparse

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/visit_tracker_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# Tests must not depend on live RxNav or accidentally spend time on network
# fallbacks. Dedicated fallback tests inject a mock HTTP transport instead.
os.environ["RXNAV_FALLBACK_ENABLED"] = "false"


def _ensure_database(url: str) -> None:
    parsed = urlparse(url)
    dbname = (parsed.path or "").lstrip("/")
    admin_url = parsed._replace(path="/postgres").geturl()
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))


@pytest.fixture(scope="session")
def client() -> Generator[TestClient, None, None]:
    _ensure_database(TEST_DATABASE_URL)
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def clean_catalog(client):
    """Reset the medication catalog to just the demo concepts.

    ``POST /api/seed`` deliberately preserves medications (an imported RxNorm
    catalog should survive a demo-data reload), so tests that assert on catalog
    size have to clear it themselves.
    """
    from app.database import SessionLocal
    from app.models import Medication

    db = SessionLocal()
    try:
        db.query(Medication).delete()
        db.commit()
    finally:
        db.close()
    client.post("/api/seed")
    return client

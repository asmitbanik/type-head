"""API integration tests."""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.db import QueryStore
from app.main import app
from app.service import SuggestionService


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "api_test.db"
    monkeypatch.setattr("app.db.DB_PATH", db_path)
    monkeypatch.setattr("app.config.DB_PATH", db_path)

    store = QueryStore(db_path=db_path)
    store.bulk_upsert([
        ("iphone", 100000),
        ("iphone 15", 85000),
        ("iphone charger", 60000),
        ("java tutorial", 40000),
        ("java", 50000),
        ("the", 500000),
        ("of", 400000),
    ])

    svc = SuggestionService()
    svc.store = store
    main_module.service = svc
    svc.trie.build(store.get_all())
    svc.batch.start()

    with TestClient(app) as c:
        yield c

    svc.shutdown()


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_suggest_prefix(client):
    r = client.get("/suggest", params={"q": "iph"})
    assert r.status_code == 200
    data = r.json()
    suggestions = data["suggestions"]
    assert len(suggestions) <= 10
    assert all(s["query"].startswith("iph") for s in suggestions)
    counts = [s["count"] for s in suggestions]
    assert counts == sorted(counts, reverse=True)


def test_suggest_empty(client):
    r = client.get("/suggest", params={"q": ""})
    assert r.status_code == 200
    assert len(r.json()["suggestions"]) > 0


def test_search(client):
    r = client.post("/search", json={"query": "brand new query"})
    assert r.status_code == 200
    body = r.json()
    assert body["message"] == "Searched"
    assert body["query"] == "brand new query"


def test_search_empty(client):
    r = client.post("/search", json={"query": "   "})
    assert r.status_code == 400


def test_cache_debug(client):
    r = client.get("/cache/debug", params={"prefix": "iphone"})
    assert r.status_code == 200
    data = r.json()
    assert "node" in data["basic"]
    assert "hit" in data["basic"]


def test_trending(client):
    client.post("/search", json={"query": "sequoia surge"})
    r = client.get("/trending")
    assert r.status_code == 200
    assert "trending" in r.json()


def test_enhanced_mode(client):
    client.post("/search", json={"query": "sequoia"})
    r = client.get("/suggest", params={"q": "seq", "mode": "enhanced"})
    assert r.status_code == 200

"""FastAPI application — routes and lifespan."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.service import SuggestionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

service: Optional[SuggestionService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global service
    service = SuggestionService()
    service.initialize()
    yield
    if service:
        service.shutdown()


app = FastAPI(
    title="Search Typeahead System",
    description="Prefix suggestions, distributed cache, trending, batch writes",
    version="1.0.0",
    lifespan=lifespan,
)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)


class SearchResponse(BaseModel):
    status: str
    message: str
    query: str
    count: int


@app.get("/healthz")
def healthz():
    assert service is not None
    return {
        "status": "ok",
        "terms": service.trie.size,
    }


@app.get("/suggest")
def suggest(
    q: str = Query("", description="Prefix to match"),
    mode: Literal["basic", "enhanced"] = Query("basic"),
):
    assert service is not None
    try:
        results = service.suggest(q, mode=mode)
        return {"prefix": q, "mode": mode, "suggestions": results}
    except Exception as e:
        logger.exception("suggest error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search", response_model=SearchResponse)
def search(body: SearchRequest):
    assert service is not None
    try:
        result = service.search(body.query)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/trending")
def trending(n: int = Query(10, ge=1, le=50)):
    assert service is not None
    return {"trending": service.get_trending(n)}


@app.get("/cache/debug")
def cache_debug(prefix: str = Query(..., alias="prefix")):
    """Debug cache routing — assignment-required endpoint."""
    assert service is not None
    from app.db import normalize_query
    norm = normalize_query(prefix)
    basic = service.cache.route(norm, "basic")
    enhanced = service.cache.route(norm, "enhanced")
    return {
        "prefix": norm,
        "basic": basic,
        "enhanced": enhanced,
    }


@app.get("/cache/route")
def cache_route(q: str = Query(""), mode: Literal["basic", "enhanced"] = "basic"):
    """Alias for cache debug (documented in README)."""
    assert service is not None
    from app.db import normalize_query
    norm = normalize_query(q) if q else ""
    return service.cache.route(norm, mode)


@app.post("/cache/nodes/{name}")
def add_cache_node(name: str):
    assert service is not None
    return service.cache.add_node(name)


@app.delete("/cache/nodes/{name}")
def remove_cache_node(name: str):
    assert service is not None
    return service.cache.remove_node(name)


@app.post("/flush")
def flush():
    assert service is not None
    return service.flush()


@app.get("/stats")
def stats():
    assert service is not None
    return service.stats()


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "Search Typeahead API — see /docs"}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

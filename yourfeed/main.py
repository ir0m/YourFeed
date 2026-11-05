"""HTTP entry point: the API and the built web app."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.settings import router as settings_router
from .api.timeline import router as timeline_router
from .config import allowed_hosts, extra_hosts
from .db import get_engine

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
EXPECTED_REVISION = "0004_translations"
MAX_BODY_BYTES = 1_000_000
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; manifest-src 'self'; object-src 'none'; "
       "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")

app = FastAPI(title="YourFeed", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())


def same_origin(request: Request) -> bool:
    """Without a login, API calls must come from this app's own pages (or a non-browser client)."""
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None and site not in {"same-origin", "none"}:
        return False
    if request.method in {"GET", "HEAD"}:
        return True
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"}:
        return False
    # A reverse proxy may rewrite Host, so configured public names are accepted as origins too.
    return parts.netloc == request.headers.get("Host") or (parts.hostname or "") in extra_hosts()


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path.startswith("/v1/") and not same_origin(request):
        return JSONResponse(status_code=403, content={"detail": "cross-origin request"})
    if int(request.headers.get("Content-Length") or 0) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "request too large"})
    response = await call_next(request)
    if request.url.path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = CSP
    return response


app.include_router(timeline_router)
app.include_router(settings_router)


@app.get("/health")
def health(engine: Engine = Depends(get_engine)) -> JSONResponse:
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "database_unavailable"})
    if revision != EXPECTED_REVISION:
        return JSONResponse(status_code=503, content={"status": "migration_needed"})
    return JSONResponse(content={"status": "ok"})


if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    def _index() -> FileResponse:
        return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-store"})

    for page in ("/", "/items/{entry_id}", "/settings", "/settings/genres/{genre_id}"):
        app.add_api_route(page, _index, include_in_schema=False)

    def _static(name: str):
        def serve() -> FileResponse:
            return FileResponse(WEB_DIST / name)
        return serve

    for name in ("manifest.webmanifest", "icon.svg"):
        app.add_api_route(f"/{name}", _static(name), include_in_schema=False)

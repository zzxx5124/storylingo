"""小說朗讀 v3 - FastAPI 後端（組裝層：薄層）。"""
import logging
import logging.handlers
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from . import db, jobs, pipeline, settings
from . import auth
from .exceptions import CodedHTTPException
from .routers import admin as admin_router_mod, auth as auth_router_mod, books as books_router_mod, voice as voice_router_mod, platform as platform_router_mod, character_resolution as character_resolution_router_mod, content_requests as content_requests_router_mod, ownership_transfer as ownership_transfer_router_mod, announcements as announcements_router_mod
from .services import notifications as notification_service
from .services.character_resolution import CharacterResolutionError, RegistryConflictError


def _setup_logging():
    log_dir = os.path.join(settings.ROOT_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "server.log"), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    logging.getLogger("uvicorn.access").propagate = False


@asynccontextmanager
async def _lifespan(_app):
    _setup_logging()
    settings.validate_env()
    db.init_db()
    from .services import analysis as analysis_svc
    recovered = analysis_svc.recover_orphaned_analysis_jobs()
    if recovered:
        logging.getLogger("storylingo.lifecycle").warning(
            "recovered orphaned analysis jobs after startup: %s", recovered)
    db.requeue_stale_generation_jobs()
    notification_service.recover_generation_notifications()
    pipeline.recover_stuck_states()
    jobs.start_worker()
    try:
        yield
    finally:
        jobs.stop_worker()
        db.close_all()


app = FastAPI(title="小說朗讀 v3", lifespan=_lifespan)


@app.exception_handler(RegistryConflictError)
async def registry_conflict_handler(_request, error):
    return JSONResponse({"detail": str(error), "code": "stale_registry_revision"}, status_code=409)


@app.exception_handler(CharacterResolutionError)
async def character_resolution_handler(_request, error):
    return JSONResponse({"detail": str(error), "code": "character_resolution_error"}, status_code=400)


@app.exception_handler(CodedHTTPException)
async def coded_http_error_handler(_request, error):
    return JSONResponse({"detail": error.detail, "code": error.code}, status_code=error.status_code,
                        headers=error.headers)


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    if settings.APP_ENV == "production" and request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        exempt = {"/api/auth/login", "/api/auth/register", "/api/auth/csrf"}
        if request.url.path not in exempt and request.cookies.get(auth.COOKIE_NAME):
            cookie_token = request.cookies.get(auth.CSRF_COOKIE)
            header_token = request.headers.get("X-CSRF-Token")
            if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "CSRF token 不正確"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if settings.APP_ENV == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' blob: https:; style-src 'self' 'unsafe-inline'; script-src 'self' https://static.cloudflareinsights.com; script-src-elem 'self' https://static.cloudflareinsights.com; connect-src 'self' https:; frame-ancestors 'none'")
    return response


@app.middleware("http")
async def release_cache_middleware(request: Request, call_next):
    """Keep the SPA shell fresh while allowing reviewed asset caching."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        # A route may provide a narrower, explicit policy (for example a
        # public image); otherwise API data must never become shared cache data.
        response.headers.setdefault("Cache-Control", "private, no-store")
    elif path in {"/", "/index.html", "/sw.js", "/manifest.webmanifest", "/service-worker.js"}:
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        # Cloudflare may cache/revalidate an origin `no-cache` response under
        # Origin Cache Control.  Keep release metadata out of the CDN cache
        # as well, so a stale Service Worker URL cannot pin an old release.
        response.headers["Cloudflare-CDN-Cache-Control"] = "no-store"
        response.headers["CDN-Cache-Control"] = "no-store"
    elif request.query_params.get("v") or request.query_params.get("release"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    else:
        response.headers.setdefault("Cache-Control", "public, max-age=0, must-revalidate")
    response.headers.setdefault("X-StoryLingo-Release", settings.RELEASE_ID)
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.PUBLIC_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

app.include_router(auth_router_mod.router)
app.include_router(books_router_mod.router)
app.include_router(voice_router_mod.router)
app.include_router(admin_router_mod.router)
app.include_router(platform_router_mod.router)
app.include_router(character_resolution_router_mod.router)
app.include_router(content_requests_router_mod.router)
app.include_router(ownership_transfer_router_mod.router)
app.include_router(announcements_router_mod.router)
app.include_router(announcements_router_mod.admin_router)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "novel-reader-v3", "releaseId": settings.RELEASE_ID,
            "db": os.path.exists(settings.DB_PATH)}


@app.get("/api/health/live")
def health_live():
    return {"ok": True, "service": "novel-reader-v3", "releaseId": settings.RELEASE_ID}


@app.get("/api/health/ready")
def health_ready():
    worker = jobs.worker_status()
    checks = {"db": False, "worker": worker["consumerReady"], "workerEnabled": worker["enabled"]}
    try:
        db.query_one("SELECT 1 AS ok")
        checks["db"] = True
    except Exception:
        checks["db"] = False
    # development 可沒有 provider；production 必須先設定正式遠端 TTS。
    checks["tts"] = settings.APP_ENV != "production" or db.get_active_tts_provider() is not None
    # 明確停用 worker 的 web-only instance 仍可 ready；enabled worker 則必須存活。
    ok = checks["db"] and (not worker["enabled"] or checks["worker"]) and checks["tts"]
    from fastapi.responses import JSONResponse
    return JSONResponse({"ok": ok, "checks": checks, "worker": worker}, status_code=200 if ok else 503)


# ---------- 前端 ----------
os.makedirs(settings.FRONTEND_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=settings.FRONTEND_DIR, html=True), name="frontend")

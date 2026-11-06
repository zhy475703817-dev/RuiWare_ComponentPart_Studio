import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel
from starlette.routing import Route

from .config import PROJECT_ROOT, Settings, load_settings
from .components.repository import DraftRepository
from .components.publications import ComponentPublications
from .components.router import router as draft_router, publication_router
from .components.service import ComponentError
from .library import LibraryError, router
from .library_cache import LibraryCache
from .library_sync import LibrarySync, router as cached_library_router
from .library_cleanup import router as cleanup_router
from .cleanup_store import CleanupStore
from .reference_registry import ReferenceRegistry, router as reference_router
from .published_storage import PublishedStorage
from .preview import PreviewCache, router as preview_router
from .reconstruction import ReconstructionCache, router as reconstruction_router
from .mcp.server import MCPAccess, create_mcp


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "component-template-api"


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    dist_dir: Path | None = PROJECT_ROOT / "dist",
    draft_db: Path | None = None,
) -> FastAPI:
    resolved_settings = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = resolved_settings
        app.state.preview_cache = PreviewCache()
        app.state.reconstruction_slots = asyncio.Semaphore(1)
        app.state.reconstruction_cache = ReconstructionCache()
        app.state.drafts = DraftRepository(draft_db or resolved_settings.draft_db)
        app.state.cleanup_store = CleanupStore(resolved_settings.cleanup_dir or (draft_db or resolved_settings.draft_db).parent / "template-cleanup")
        app.state.cleanup_lock = RLock()
        app.state.published_storage = PublishedStorage(resolved_settings.minio)
        app.state.publications = ComponentPublications(app.state.drafts, resolved_settings.minio, app.state.published_storage)
        app.state.library_cache = LibraryCache(draft_db or resolved_settings.draft_db, resolved_settings)
        app.state.library_sync = LibrarySync(app)
        app.state.reference_registry = ReferenceRegistry(app.state.drafts, app.state.published_storage)
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False, transport=transport) as client:
            app.state.http = client
            async with mcp.session_manager.run():
                app.state.reference_registry.start()
                try:
                    yield
                finally:
                    await app.state.reference_registry.close()
                    await app.state.library_sync.close()

    app = FastAPI(title="组件模板平台 API", version="0.1.0", lifespan=lifespan)
    mcp = create_mcp(app, resolved_settings.public_base_url)
    mcp_http = mcp.streamable_http_app()

    @app.exception_handler(ComponentError)
    async def component_error(_request: Request, error: ComponentError):
        return JSONResponse(error.as_dict(), status_code=error.status, headers={"Cache-Control": "no-store"})

    @app.exception_handler(LibraryError)
    async def library_error(_request: Request, error: LibraryError):
        return JSONResponse({"error": {"code": error.code}}, status_code=error.status, headers={"Cache-Control": "no-store"})

    @app.get("/api/health", tags=["服务"], response_model=HealthResponse, summary="检查组件后端是否运行")
    async def health() -> HealthResponse:
        return HealthResponse()

    app.include_router(router)
    app.include_router(cached_library_router)
    app.include_router(cleanup_router)
    app.include_router(reference_router)
    app.include_router(preview_router)
    app.include_router(reconstruction_router)
    app.include_router(draft_router)
    app.include_router(publication_router)
    app.router.routes.append(Route("/mcp", endpoint=MCPAccess(mcp_http, app), methods=["GET", "POST", "DELETE"]))

    if dist_dir is not None and (dist_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="workbench")
    return app


app = create_app()

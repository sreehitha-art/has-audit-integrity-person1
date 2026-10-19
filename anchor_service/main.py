"""Independent anchoring service (port 8001). Run:  python -m anchor_service.main
FastAPI layer: auth, HTTP status mapping, fault-injection transport behaviour.
UNTESTED in the build sandbox (FastAPI not installable offline) - see tests/test_api.py.
"""
from __future__ import annotations

import asyncio
import hmac
from typing import Any

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse

from research import telemetry as tel
from .config import Settings
from .services.core import AnchorCore, AnchorError, BadRequest, Repository
from .services.trusted_keys import load_trusted_keys


def _eq(a: str, b: str) -> bool:
    return bool(a) and hmac.compare_digest(a.encode(), b.encode())


def create_app(settings: Settings | None = None, repo: Repository | None = None,
               trusted_keys: dict | None = None) -> FastAPI:
    st = settings or Settings.load()
    if repo is None:
        from .database import make_engine, make_session_factory
        from .services.sql_repository import SqlRepository
        repo = SqlRepository(make_session_factory(make_engine(st.database_url)))
    keys = trusted_keys if trusted_keys is not None else load_trusted_keys(st.trusted_keys_dir)
    if st.run_id:
        tel.start_run(st.run_id, st.raw_root)
    core = AnchorCore(repo, keys, on_fault_event=lambda t, **k: tel.emit(t, **k))
    app = FastAPI(title="HMS Anchor Service", version="0.1.0")
    app.state.core, app.state.settings = core, st

    @app.exception_handler(AnchorError)
    async def _anchor_error(_: Request, exc: AnchorError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    def auth(authorization: str | None = Header(default=None)):
        if not st.api_token:
            raise AnchorError("server has no ANCHOR_API_TOKEN configured")        # fail closed (500)
        scheme, _, tok = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not _eq(tok, st.api_token):
            raise _Unauthorized()

    class _Unauthorized(AnchorError):
        status, code = 401, "unauthorized"
        def __init__(self): super().__init__("missing or invalid bearer token")

    @app.post("/v1/checkpoints", status_code=201)
    async def post_checkpoint(request: Request, _=Depends(auth)):
        try:
            body: Any = await request.json()
        except Exception:
            raise BadRequest("request body is not valid JSON")
        # core does blocking work (DB, sleeps for delay faults) -> thread pool
        rec, created, post_delay = await asyncio.to_thread(core.ingest_with_fault, body)
        if post_delay:                      # drop_response: record is ALREADY committed
            await asyncio.sleep(post_delay)
        return JSONResponse({"receipt": rec.receipt(), "created": created},
                            status_code=201 if created else 200)

    @app.get("/v1/chains/{chain_id}/checkpoints")
    async def list_chain(chain_id: str, _=Depends(auth)):
        recs = await asyncio.to_thread(repo.list_chain, chain_id)
        return {"chain_id": chain_id, "checkpoints": [r.as_dict() for r in recs]}

    @app.get("/v1/checkpoints/{anchor_id}")
    async def get_one(anchor_id: str, _=Depends(auth)):
        rec = await asyncio.to_thread(repo.get_by_id, anchor_id)
        if rec is None:
            return JSONResponse({"error": {"code": "not_found", "message": "unknown anchor_id"}}, status_code=404)
        return rec.as_dict()

    @app.get("/health/ready")
    async def ready():
        ok = await asyncio.to_thread(repo.ping)
        # readiness only: says nothing about whether any particular checkpoint is retained
        return JSONResponse({"status": "ready" if ok else "not_ready", "database": ok,
                             "trusted_keys_loaded": len(keys),
                             "note": "readiness does not imply any checkpoint is retained"},
                            status_code=200 if ok else 503)

    @app.post("/v1/_test/fault")
    async def set_fault(request: Request, x_test_token: str | None = Header(default=None)):
        # Hidden (404) unless test mode; then require BOTH test token header. Never a prod control.
        if not st.test_mode:
            return JSONResponse({"error": {"code": "not_found", "message": "not found"}}, status_code=404)
        if not st.test_token or not _eq(x_test_token or "", st.test_token):
            raise _Unauthorized()
        try:
            data = await request.json()
        except Exception:
            raise BadRequest("request body is not valid JSON")
        if not isinstance(data, dict):
            raise BadRequest("body must be a JSON object")
        return core.set_fault(data.get("mode"), data.get("delay_seconds", 0.0))

    return app


if __name__ == "__main__":      # pragma: no cover
    import uvicorn
    s = Settings.load()
    uvicorn.run(create_app(s), host="127.0.0.1", port=s.port)

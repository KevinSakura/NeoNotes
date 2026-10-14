"""Isolated browser-test server. Never writes to the user's data directory."""

import sys
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.main import create_app  # noqa: E402
from backend.app.model_settings import ModelSettings  # noqa: E402

if __name__ == "__main__":
    run_id = uuid4().hex
    data_dir = ROOT / ".cache" / "e2e-runs" / run_id / "data"
    assert data_dir.resolve().is_relative_to((ROOT / ".cache" / "e2e-runs").resolve())
    app = create_app(data_dir, model_settings=ModelSettings(mock=True))
    # Test-only fault injection into the deterministic mock service, never production.
    phrase_controls = {}
    original_phrase_call = app.state.generator.phrase_runner._call

    def controlled_phrase_call(project_id, job_id, context, settings, stage, raw_mock, validate):
        if not settings.mock:
            raise RuntimeError("Browser tests require an injected mock configuration.")
        if stage.startswith("block-") and context.get("block_index") == phrase_controls.get(project_id):
            from backend.app.model_settings import ModelError

            def failure():
                raise ModelError("隔离模拟服务：这个分块失败。", code="mock_block_failure")

            raw_mock = failure
        return original_phrase_call(project_id, job_id, context, settings, stage, raw_mock, validate)

    app.state.generator.phrase_runner._call = controlled_phrase_call

    # A browser can close a streamed WAV connection on Windows while FileResponse
    # is sending. Bound draining so the test-only shutdown cannot wait forever.
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=18000, workers=1, timeout_graceful_shutdown=5)
    )

    # Only this isolated server has these routes. Production cannot be stopped through HTTP.
    @app.get("/api/test-server")
    def identity():
        return {"isolated_browser_test": True, "run_id": run_id}

    @app.post("/api/test-server/shutdown")
    def shutdown(body: dict):
        if body.get("run_id") != run_id:
            raise HTTPException(403, "Wrong isolated test run.")
        server.should_exit = True
        return {"stopping": True}

    @app.get("/api/test-server/vocal-fixture")
    def vocal_fixture():
        from scripts.check_vocals import fixture

        return fixture(tempo_change=True).model_dump()

    @app.post("/api/test-server/phrase-control")
    def phrase_control(body: dict):
        if body.get("run_id") != run_id:
            raise HTTPException(403, "Wrong isolated test run.")
        app.state.store.header(body["project_id"])
        block = body.get("fail_block")
        if block is not None and (type(block) is not int or not 0 <= block < 16):
            raise HTTPException(422, "Invalid mock block.")
        phrase_controls[body["project_id"]] = block
        return {"mock_only": True}

    # Register the test-only endpoints before the application's SPA fallback route.
    app.router.routes = app.router.routes[-4:] + app.router.routes[:-4]
    server.run()

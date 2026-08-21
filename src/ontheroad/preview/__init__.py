"""Preview reverse proxy (Phase 2, slice 2C).

Mount in `create_app()` (integration pass):

    from ontheroad.preview import PreviewTokenMiddleware, router as preview_router
    app.include_router(preview_router)
    # AFTER app.add_middleware(AuthMiddleware) so token promotion runs first:
    app.add_middleware(PreviewTokenMiddleware)
"""

from ontheroad.preview.proxy import PreviewTokenMiddleware, router

__all__ = ["PreviewTokenMiddleware", "router"]

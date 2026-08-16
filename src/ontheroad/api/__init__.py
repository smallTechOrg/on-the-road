"""REST + WebSocket API routers.

Wiring (done by main.py's lifespan, owned by slice 1A/1E):

    from ontheroad.api.sessions import router as sessions_router
    from ontheroad.api.stream import router as stream_router
    from ontheroad.sessions import EventStore, SessionManager
    from ontheroad import db

    conn = await db.connect(settings.db_path)
    manager = SessionManager(EventStore(conn))
    await manager.startup()
    app.state.session_manager = manager
    app.include_router(sessions_router)
    app.include_router(stream_router)
"""

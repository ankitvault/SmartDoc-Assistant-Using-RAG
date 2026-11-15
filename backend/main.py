"""
Main FastAPI Application Entry Point
Initializes the FastAPI app, connects to MongoDB, and registers all route routers.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


try:
    from backend.routes import user, auth, document, chat
    from backend.config import CORS_ALLOW_ORIGINS, CORS_ALLOW_CREDENTIALS
except ModuleNotFoundError:
    from routes import user, auth, document, chat
    from config import CORS_ALLOW_ORIGINS, CORS_ALLOW_CREDENTIALS

app = FastAPI(title="SmartDOC Assistant API", version="1.0.0")
FRONTEND_DIST_DIR = Path(__file__).resolve().parents[1] / "frontend" / "dist"

# Enable CORS for local and hosted frontend apps (Vite/React, Vercel deployments).
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {str(exc)}"},
    )


# API routes
app.include_router(user.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(document.router, prefix="/api")
app.include_router(chat.router, prefix="/api")

# React build
app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")

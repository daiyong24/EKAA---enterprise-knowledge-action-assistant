from fastapi import APIRouter

from backend.app.api.routes.admin import router as admin_router
from backend.app.api.routes.approvals import router as approvals_router
from backend.app.api.routes.chat import router as chat_router
from backend.app.api.routes.documents import router as documents_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.metrics import router as metrics_router
from backend.app.api.routes.rag import router as rag_router
from backend.app.api.routes.retrieval import router as retrieval_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(chat_router, prefix="/api")
api_router.include_router(documents_router, prefix="/api")
api_router.include_router(retrieval_router, prefix="/api")
api_router.include_router(rag_router, prefix="/api")
api_router.include_router(admin_router, prefix="/api")
api_router.include_router(approvals_router, prefix="/api")
api_router.include_router(metrics_router)

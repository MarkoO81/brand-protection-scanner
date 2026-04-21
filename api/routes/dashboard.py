"""GET / → serve the dashboard UI."""
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", include_in_schema=False)
async def dashboard():
    return FileResponse("dashboard/templates/index.html")

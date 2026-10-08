"""GET /catalog — any signed-in user (FR-028)."""

from fastapi import APIRouter, Depends

from ..auth.deps import CurrentUser, current_user
from . import catalog

router = APIRouter(tags=["catalog"])


@router.get("/catalog")
async def list_catalog(_: CurrentUser = Depends(current_user)) -> list[dict]:  # noqa: B008
    return catalog.public()

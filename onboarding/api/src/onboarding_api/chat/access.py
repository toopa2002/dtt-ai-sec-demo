"""Participant check shared by session, chat, attachment and stream routes (404 for non-participants)."""

from fastapi import status

from ..auth.deps import CurrentUser, error
from ..sessions import repo


async def participant_session(session_id: str, user: CurrentUser) -> tuple[dict, str]:
    session = await repo.get(session_id)
    role = repo.participant_role(session, user.id) if session else None
    if not session or not role:
        raise error("not_found", "No such session.", status.HTTP_404_NOT_FOUND)
    return session, role

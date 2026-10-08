"""/auth/login, /auth/logout, /auth/session (contracts/session-api.openapi.yaml; FR-001, FR-004)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from ..audit import audit
from ..config import settings
from ..db import db
from . import sessions, users
from .deps import CurrentUser, current_user, error

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


def me(user: dict, csrf: str) -> dict:
    out = users.public(user)
    return {k: out[k] for k in ("id", "username", "display_name", "role", "is_admin")} | {"csrf_token": csrf}


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        sessions.COOKIE, token, httponly=True, secure=settings().cookie_secure, samesite="strict",
        path="/onboarding/", max_age=settings().idle_minutes * 60 * 48,
    )


@router.post("/login")
async def login(body: LoginBody, response: Response) -> dict:
    s = settings()
    now = datetime.now(UTC)
    user = await users.get_by_username(body.username.strip().lower())
    generic = error("invalid_credentials", "Wrong username or password.", status.HTTP_401_UNAUTHORIZED)
    if not user:
        await audit.record("sign_in_failed", username_tried=body.username[:64])
        users.verify_password(users.hash_password("timing-equaliser"), body.password)
        raise generic
    if user["status"] == "disabled":
        await audit.record("sign_in_failed", user["_id"], detail={"reason": "disabled"})
        raise generic
    if user.get("locked_until") and user["locked_until"] > now:
        retry = int((user["locked_until"] - now).total_seconds())
        raise error_with_retry("account_locked", "This account is locked. Try again later.", retry)
    if not users.verify_password(user["password_hash"], body.password):
        attempts = user.get("failed_attempts", 0) + 1
        update: dict = {"failed_attempts": attempts}
        if attempts >= s.lockout_attempts:
            update.update(failed_attempts=0, locked_until=now + timedelta(minutes=s.lockout_minutes))
            await db().users.update_one({"_id": user["_id"]}, {"$set": update})
            await audit.record("locked", user["_id"])
            raise error_with_retry("account_locked", f"Too many attempts. This account is locked for "
                                   f"{s.lockout_minutes} minutes.", s.lockout_minutes * 60)
        await db().users.update_one({"_id": user["_id"]}, {"$set": update})
        await audit.record("sign_in_failed", user["_id"])
        left = s.lockout_attempts - attempts
        raise error("invalid_credentials", f"Wrong username or password. {left} attempt{'s' if left != 1 else ''} "
                    f"left before this account is locked for {s.lockout_minutes} minutes.",
                    status.HTTP_401_UNAUTHORIZED)
    await db().users.update_one({"_id": user["_id"]},
                                {"$set": {"failed_attempts": 0, "locked_until": None, "last_sign_in": now}})
    token, csrf = await sessions.create(user["_id"])
    _set_cookie(response, token)
    await audit.record("sign_in", user["_id"])
    return me(user, csrf)


def error_with_retry(code: str, message: str, retry_after: int):  # type: ignore[no-untyped-def]
    exc = error(code, message, status.HTTP_423_LOCKED)
    exc.detail["retry_after_seconds"] = retry_after  # type: ignore[index]
    return exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, user: CurrentUser = Depends(current_user)) -> None:  # noqa: B008
    await sessions.revoke(user.token)
    response.delete_cookie(sessions.COOKIE, path="/onboarding/")


@router.get("/session")
async def session(user: CurrentUser = Depends(current_user)) -> dict:  # noqa: B008
    return me(user.doc, user.csrf)

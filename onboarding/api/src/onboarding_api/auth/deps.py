"""Request dependencies: current user, role and admin checks, CSRF (FR-003)."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from . import sessions, users


@dataclass
class CurrentUser:
    doc: dict
    csrf: str
    token: str

    @property
    def id(self):  # type: ignore[no-untyped-def]
        return self.doc["_id"]

    @property
    def role(self) -> str:
        return self.doc["role"]

    @property
    def is_admin(self) -> bool:
        return bool(self.doc.get("is_admin"))


def error(code: str, message: str, http_status: int) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, "message": message})


async def current_user(request: Request) -> CurrentUser:
    token = request.cookies.get(sessions.COOKIE)
    if not token:
        raise error("not_signed_in", "Sign in to continue.", status.HTTP_401_UNAUTHORIZED)
    sess = await sessions.touch(token)
    if not sess:
        raise error("not_signed_in", "Your session ended. Sign in again.", status.HTTP_401_UNAUTHORIZED)
    user = await users.get_user(sess["user_id"])
    if not user or users.effective_status(user) != "active":
        await sessions.revoke(token)
        raise error("not_signed_in", "This account can't sign in.", status.HTTP_401_UNAUTHORIZED)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-csrf-token") != sess["csrf"]:
        raise error("csrf", "Reload the page and try again.", status.HTTP_403_FORBIDDEN)
    return CurrentUser(doc=user, csrf=sess["csrf"], token=token)


def require_role(role: str):  # type: ignore[no-untyped-def]
    async def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:  # noqa: B008
        if user.role != role:
            raise error("forbidden_role", "Your role can't do this.", status.HTTP_403_FORBIDDEN)
        return user

    return dep


async def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:  # noqa: B008
    if not user.is_admin:
        raise error("forbidden_role", "Only admins can do this.", status.HTTP_403_FORBIDDEN)
    return user

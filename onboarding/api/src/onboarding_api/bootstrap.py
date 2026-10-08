"""Create the first admin from a one-time secret (FR-002, research R8).

Run as a one-off Kubernetes Job: `python -m onboarding_api.bootstrap`. Reads ONB_ADMIN_USERNAME,
ONB_ADMIN_DISPLAY_NAME and ONB_ADMIN_PASSWORD. Does nothing when an admin already exists.
"""

import asyncio
import os
import sys

from . import db
from .auth import users


async def main() -> int:
    await db.ensure_indexes()
    if await db.db().users.find_one({"is_admin": True}):
        print("an admin already exists; nothing to do")
        return 0
    username = os.environ["ONB_ADMIN_USERNAME"].strip().lower()
    display = os.environ.get("ONB_ADMIN_DISPLAY_NAME", username)
    password = os.environ["ONB_ADMIN_PASSWORD"]
    try:
        await users.create_user(username, display, "iam_engineer", password, is_admin=True)
    except users.UserError as exc:
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        return 1
    print(f"created admin {username}")
    await db.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

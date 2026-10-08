"""MongoDB connection and index bootstrap (data-model.md). No collection stores a secret."""

from gridfs.asynchronous import AsyncGridFSBucket
from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from .config import settings

_client: AsyncMongoClient | None = None


def client() -> AsyncMongoClient:
    global _client
    if _client is None:
        _client = AsyncMongoClient(settings().mongo_uri, tz_aware=True)
    return _client


def db() -> AsyncDatabase:
    return client()[settings().mongo_db]


def screenshots() -> AsyncGridFSBucket:
    return AsyncGridFSBucket(db(), bucket_name="screenshots")


async def migrate_threads() -> int:
    """One-off (research R18): give messages from the single-chat version a `thread` and `kind`.

    A participant message goes to the speaker's thread; an agent message with `addressed_to` of a participant to
    that thread; any other agent message to the thread of the participant message it answered (same `turn_id`, or
    the nearest earlier participant message when the turn is not recorded). Only touches documents without
    `thread`, so running it again changes nothing. Returns the number of messages migrated.
    """
    d = db()
    missing = {"thread": {"$exists": False}}
    count = 0
    for role in ("iam_engineer", "application_owner"):
        r = await d.messages.update_many(missing | {"speaker": role}, {"$set": {"thread": role, "kind": "message"}})
        count += r.modified_count
        r = await d.messages.update_many(missing | {"speaker": "agent", "addressed_to": role},
                                         {"$set": {"thread": role, "kind": "message"}})
        count += r.modified_count
    async for m in d.messages.find(missing | {"speaker": "agent"}):
        asker = None
        if m.get("turn_id"):
            asker = await d.messages.find_one({"session_id": m["session_id"], "turn_id": m["turn_id"],
                                               "speaker": {"$ne": "agent"}})
        if not asker:
            asker = await d.messages.find_one({"session_id": m["session_id"], "seq": {"$lt": m["seq"]},
                                               "speaker": {"$ne": "agent"}}, sort=[("seq", -1)])
        thread = asker["speaker"] if asker else "iam_engineer"
        await d.messages.update_one({"_id": m["_id"]}, {"$set": {"thread": thread, "kind": "message"}})
        count += 1
    return count


async def ensure_indexes() -> None:
    d = db()
    await d.users.create_index("username", unique=True)
    await d.auth_sessions.create_index("expires_at", expireAfterSeconds=0)
    await d.auth_sessions.create_index("user_id")
    await d.tenants.create_index("name", unique=True)
    await d.sessions.create_index([("iam_engineer_id", ASCENDING), ("status", ASCENDING)])
    await d.sessions.create_index([("application_owner_id", ASCENDING), ("status", ASCENDING)])
    await d.sessions.create_index("expires_at", expireAfterSeconds=0)
    await d.messages.create_index([("session_id", ASCENDING), ("seq", ASCENDING)], unique=True)
    await d.messages.create_index([("session_id", ASCENDING), ("thread", ASCENDING), ("seq", ASCENDING)])
    await d.messages.create_index("expires_at", expireAfterSeconds=0)
    await d.events.create_index([("session_id", ASCENDING), ("event_id", ASCENDING)], unique=True)
    await d.events.create_index("expires_at", expireAfterSeconds=0)
    await d.attachments.create_index("session_id")
    await d.attachments.create_index("expires_at", expireAfterSeconds=0)
    # actions and audit are kept under the organisation's audit retention: no TTL (research R10).
    await d.actions.create_index([("session_id", ASCENDING), ("at", ASCENDING)])
    await d.audit.create_index("at")
    await migrate_threads()


async def close() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None

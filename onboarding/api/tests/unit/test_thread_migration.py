"""T090: the one-off thread migration (research R18) is correct and idempotent."""

from datetime import UTC, datetime

from bson import ObjectId

from onboarding_api import db


async def test_old_messages_get_a_thread_once() -> None:
    sid = ObjectId()
    now = datetime.now(UTC)

    def old(seq: int, speaker: str, **extra):  # type: ignore[no-untyped-def]
        return {"session_id": sid, "seq": seq, "speaker": speaker, "text": f"m{seq}", "created_at": now, **extra}

    await db.db().messages.insert_many([
        old(1, "application_owner"),
        old(2, "agent", addressed_to="application_owner"),
        old(3, "iam_engineer"),
        old(4, "agent", addressed_to="both", turn_id="t-iam"),      # answered the IAM engineer's message 3
        old(5, "agent"),                                              # no addressee, no turn: nearest earlier writer
        old(6, "iam_engineer", turn_id="t-iam"),
    ])
    migrated = await db.migrate_threads()
    assert migrated == 6
    docs = {m["seq"]: m async for m in db.db().messages.find({"session_id": sid})}
    assert docs[1]["thread"] == "application_owner"
    assert docs[2]["thread"] == "application_owner"
    assert docs[3]["thread"] == "iam_engineer"
    assert docs[4]["thread"] == "iam_engineer"   # the turn's participant message (seq 6) is the IAM engineer's
    assert docs[5]["thread"] == "iam_engineer"   # nearest earlier participant message is seq 3
    assert all(m["kind"] == "message" for m in docs.values())
    assert await db.migrate_threads() == 0
    await db.db().messages.delete_many({"session_id": sid})

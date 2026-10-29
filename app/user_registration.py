"""Convert private registration relay messages into the existing event envelope."""
import re
from datetime import datetime
from uuid import UUID

from app.models import Event


def registration_event(row: dict) -> Event:
    try:
        event_id = str(UUID(row["id"]))
        session_id = str(UUID(row["gameSessionId"]))
        username = row["username"]
        timestamp = row["createdAt"]
        if not isinstance(username, str) or not re.fullmatch(r"[a-z]+[0-9]{4}", username):
            raise ValueError
        if len(username) > 80 or not isinstance(timestamp, str):
            raise ValueError
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError("Invalid user registration received from Rayfin.") from None
    return Event(
        schema_version="1.0", event_id=event_id, session_id=session_id, sequence=0,
        source_mode="registration", frame_index=None, video_time_ms=None,
        observed_at_utc=timestamp, event_type="user.register", player=None,
        payload={
            "user_session_id": event_id, "game_session_id": session_id,
            "username": username,
        },
    )

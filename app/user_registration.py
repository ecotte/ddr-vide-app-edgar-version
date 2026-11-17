"""Convert private registration relay messages into the existing event envelope."""
import re
from datetime import datetime, timezone
from uuid import UUID

from app.models import Event


def registration_event(row: dict) -> Event:
    try:
        event_id = str(UUID(row["id"]))
        session_id = str(UUID(row["gameSessionId"]))
        username = row["username"]
        registration_type = row.get("registrationType", "player")
        pin = row.get("pin")
        excluded = row.get("excludeFromLeaderboard")
        if excluded is None:
            excluded = False
        if type(excluded) is not bool:
            raise ValueError
        difficulty = row.get("difficulty")
        if difficulty is None:
            difficulty = 0 if excluded else None
        elif type(difficulty) is not int or difficulty not in (0, 3, 4) or (difficulty == 0) != excluded:
            raise ValueError
        player = row.get("player")
        if player not in (None, "P1", "P2"):
            raise ValueError
        timestamp = row["createdAt"]
        if registration_type == "special":
            if pin is not None:
                raise ValueError
            valid_name = username in ("YaelTheChampion", "EdgarIsGoingDown", "The Dancing Queen", "Bowser")
        elif registration_type == "player":
            if not isinstance(pin, str) or re.fullmatch(r"[0-9]{6}", pin) is None:
                raise ValueError
            valid_name = isinstance(username, str) and re.fullmatch(
                r"[A-Za-z][a-z]+(?:[A-Z][a-z]+)?[0-9]{4}", username,
            )
        else:
            valid_name = False
        if not valid_name:
            raise ValueError
        if len(username) > 80 or not isinstance(timestamp, str):
            raise ValueError
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError
        timestamp = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("Invalid user registration received from Rayfin.") from None
    return Event(
        schema_version="1.0", event_id=event_id, session_id=session_id, sequence=0,
        source_mode="registration", frame_index=None, video_time_ms=None,
        observed_at_utc=timestamp, event_type="user.register", player=player,
        payload={
            "user_session_id": event_id, "game_session_id": session_id,
            "username": username, "registration_type": registration_type,
            **({"pin": pin} if registration_type == "player" else {}),
            "exclude_from_leaderboard": excluded,
            "difficulty": difficulty,
        },
    )

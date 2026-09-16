"""Session/sequence bookkeeping and event construction + JSONL export.

Keeps the video's own timeline (frame_index / video_time_ms) explicitly
separate from wall-clock time (observed_at_utc), per the project spec.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import config
from app.models import Event


class EventBus:
    """Owns session_id + sequence numbering and builds Event envelopes.

    Call ``new_session()`` whenever a new video is loaded or the user
    explicitly starts a new session, per the reset rules in the spec.
    """

    def __init__(self, source_mode: str = config.SOURCE_MODE_REPLAY):
        self.source_mode = source_mode
        self.session_id: str = str(uuid.uuid4())
        self._sequence: int = 0
        self._events: List[Event] = []

    def new_session(self) -> None:
        self.session_id = str(uuid.uuid4())
        self._sequence = 0
        self._events = []

    def clear_events(self) -> None:
        """Clear the recorded/displayed events list without starting a new
        session (sequence numbering and session_id are untouched)."""
        self._events = []

    @property
    def events(self) -> List[Event]:
        return list(self._events)

    def make_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        frame_index: Optional[int],
        video_time_ms: Optional[int],
        player: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> Event:
        event = Event(
            schema_version=config.SCHEMA_VERSION,
            event_id=str(uuid.uuid4()),
            session_id=self.session_id,
            sequence=self._sequence,
            source_mode=self.source_mode,
            frame_index=frame_index,
            video_time_ms=video_time_ms,
            observed_at_utc=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            player=player,
            payload=payload,
            confidence=confidence,
        )
        self._sequence += 1
        return event

    def record(self, event: Event) -> None:
        """Append an event to the in-memory recorded list (for table/export)."""
        self._events.append(event)

    def export_jsonl(self, path: str) -> int:
        """Write all currently recorded events to ``path``, one JSON per line.

        Returns the number of events written.
        """
        with open(path, "w", encoding="utf-8") as f:
            for event in self._events:
                f.write(json.dumps(event.to_dict()))
                f.write("\n")
        return len(self._events)

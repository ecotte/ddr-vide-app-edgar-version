"""Current scoreboard calculations and direct, opt-in Rayfin publishing."""
import copy
import threading
from bisect import bisect_right, insort

from app import config
from app.models import Event
from app.rayfin_sender import (
    RayfinSender, ScoreboardConnectionError, ScoreboardPublishError, ScoreboardRegistrationError,
)


def _player():
    return {
        "score": None, "runningScore": 0, "combo": None, "hits": 0, "misses": 0,
        "unassignedHits": 0, "unassignedMisses": 0,
        "hitsByDirection": {direction: 0 for direction in config.ARROW_DIRECTIONS},
        "missesByDirection": {direction: 0 for direction in config.ARROW_DIRECTIONS},
        "hitPace": 0.0,
        "averageResponseTimeMs": None, "responseSamples": 0,
        "observedScore": None, "lastVideoTimeMs": None,
    }


class LiveScoreboard:
    def __init__(self, sender_factory=RayfinSender):
        self._sender_factory = sender_factory
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._publish_wakeup = threading.Event()
        self._thread = None
        self._error = ""
        self._seen = set()
        self._connection_status = "Live scoreboard: off"
        self._field_sequences = {}
        self._recent_hits = {"P1": [], "P2": []}
        self._response_totals = {"P1": 0, "P2": 0}
        self._video_time_ms = 0
        self._playback_clock = False
        self._state = {
            "active": False, "sessionId": "", "revision": 0,
            "players": {"P1": _player(), "P2": _player()},
        }

    @property
    def active(self):
        with self._lock:
            return self._state["active"]

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._state)

    def status(self):
        with self._lock:
            if self._error:
                return self._error
            return self._connection_status

    def _reset(self, session_id):
        self._state.update(
            sessionId=session_id, revision=self._state["revision"] + 1,
            players={"P1": _player(), "P2": _player()},
        )
        self._seen.clear()
        self._field_sequences.clear()
        self._recent_hits = {"P1": [], "P2": []}
        self._response_totals = {"P1": 0, "P2": 0}
        self._video_time_ms = 0
        self._playback_clock = False

        self._publish_wakeup.set()

    def advance_video(self, video_time_ms: int, *, is_seek: bool = False):
        """Age hit pace with playback, even when no events are recorded."""
        if type(video_time_ms) is not int or video_time_ms < 0:
            raise ValueError("Scoreboard video time must be a nonnegative integer.")
        with self._lock:
            if not self._state["active"]:
                return
            if is_seek or video_time_ms < self._video_time_ms:
                self._recent_hits = {"P1": [], "P2": []}
            self._video_time_ms = video_time_ms
            self._playback_clock = True
            self._refresh_pace()

    def _refresh_pace(self):
        for name, times in self._recent_hits.items():
            del times[:bisect_right(times, self._video_time_ms - 5000)]
            pace = len(times) / 5.0
            if self._state["players"][name]["hitPace"] != pace:
                self._state["players"][name]["hitPace"] = pace
                self._state["revision"] += 1
                self._publish_wakeup.set()

    def record(self, event: Event | None, session_id: str):
        with self._lock:
            if not self._state["active"]:
                return
            if event is None:
                self._reset(session_id)
                return
            if event.source_mode == "synthetic" or event.payload.get("is_test"):
                return
            if event.session_id != session_id or session_id != self._state["sessionId"]:
                return
            if type(event.sequence) is not int or event.sequence < 0:
                self._error = "Live scoreboard: invalid event sequence rejected."
                return
            if event.event_id in self._seen:
                return
            self._seen.add(event.event_id)
            if event.player not in self._state["players"]:
                return
            player = self._state["players"][event.player]
            if event.event_type in ("score_changed", "combo", "combo_changed", "score_observed"):
                key = (
                    "value" if event.event_type == "combo"
                    else "observed_value" if event.event_type == "score_observed" else "new_value"
                )
                value = event.payload.get(key)
                if type(value) is not int or not 0 <= value <= 9007199254740991:
                    self._error = "Live scoreboard: invalid numeric event rejected."
                    return
                field = {
                    "score_changed": "score", "combo": "combo", "combo_changed": "combo",
                    "score_observed": "observedScore",
                }[event.event_type]
                field_key = (event.player, field)
                if event.sequence <= self._field_sequences.get(field_key, -1):
                    return
                if field == "observedScore" and event.sequence <= self._field_sequences.get(
                    (event.player, "score"), -1,
                ):
                    return
                self._field_sequences[field_key] = event.sequence
                player[field] = value
                if field == "score" and self._field_sequences.get(
                    (event.player, "observedScore"), -1,
                ) < event.sequence:
                    player["observedScore"] = None
            elif event.event_type in (
                config.JUDGEMENT_EVENT_TYPE, config.ARROW_EVENT_TYPE,
                "arrow_hit", "arrow_miss", "judgment_hit", "judgment_miss",
            ):
                direction = event.payload.get("direction")
                if event.event_type == config.ARROW_EVENT_TYPE or (
                    event.event_type == "arrow_hit" and event.payload.get("evidence") == "arrow_activity"
                ):
                    if (
                        event.payload.get("state") != "active"
                        or event.payload.get("evidence") != "arrow_activity"
                        or not isinstance(direction, str) or direction not in config.ARROW_DIRECTIONS
                    ):
                        self._error = "Live scoreboard: invalid arrow activity payload rejected."
                    return
                is_judgement = event.event_type == config.JUDGEMENT_EVENT_TYPE
                judgment = event.payload.get("judgment")
                if is_judgement:
                    outcome = event.payload.get("outcome")
                    directions = event.payload.get("step_directions")
                    count = event.payload.get("step_arrow_count")
                    if (
                        outcome not in ("hit", "miss")
                        or event.payload.get("state") != outcome
                        or not isinstance(directions, list)
                        or not all(isinstance(item, str) and item in config.ARROW_DIRECTIONS for item in directions)
                        or len(set(directions)) != len(directions)
                        or type(count) is not int or count != len(directions)
                        or direction != (directions[0] if len(directions) == 1 else None)
                        or not isinstance(judgment, str)
                        or judgment not in (*config.JUDGMENT_POINTS, "hit")
                        or (judgment == "miss") != (outcome == "miss")
                    ):
                        self._error = "Live scoreboard: invalid judgement outcome payload rejected."
                        return
                    is_miss = outcome == "miss"
                    unassigned = not directions
                else:
                    unassigned = event.event_type.startswith("judgment_")
                    if (unassigned and direction is not None) or (
                        not unassigned and direction not in player["hitsByDirection"]
                    ):
                        self._error = "Live scoreboard: invalid arrow direction in outcome event."
                        return
                    directions = [] if unassigned else [direction]
                    is_miss = event.event_type.endswith("_miss")
                if judgment is not None and (
                    not isinstance(judgment, str)
                    or judgment not in (*config.JUDGMENT_POINTS, "hit")
                    or (judgment == "miss") != is_miss
                ):
                    self._error = "Live scoreboard: invalid judgment tier in outcome event."
                    return
                if event.event_type == "arrow_hit" and "judgement_event_id" in event.payload:
                    if not isinstance(event.payload["judgement_event_id"], str) or not event.payload["judgement_event_id"]:
                        self._error = "Live scoreboard: invalid judgement event link rejected."
                    # The linked judgement is authoritative even when these events arrive first.
                    return
                outcome_count = len(directions) or 1
                points = config.JUDGMENT_POINTS.get(judgment, 0) * outcome_count
                if player["runningScore"] + points > 9007199254740991:
                    self._error = "Live scoreboard: running score exceeds the supported range."
                    return
                player["runningScore"] += points
                if is_miss:
                    player["misses"] += outcome_count
                    if unassigned:
                        player["unassignedMisses"] += 1
                    else:
                        for lane in directions:
                            player["missesByDirection"][lane] += 1
                    self._state["revision"] += 1
                    self._publish_wakeup.set()
                    return
                player["hits"] += outcome_count
                if unassigned:
                    player["unassignedHits"] += 1
                else:
                    for lane in directions:
                        player["hitsByDirection"][lane] += 1
                response = event.payload.get("response_time_ms")
                if unassigned and response is not None:
                    self._error = "Live scoreboard: directionless judgment timing excluded from average."
                    response = None
                if is_judgement:
                    response = None
                if response is not None:
                    appearance = event.payload.get("appearance_video_time_ms")
                    hit = event.payload.get("hit_video_time_ms")
                    if (
                        all(type(value) is int and 0 <= value <= 9007199254740991
                            for value in (response, appearance, hit, event.video_time_ms))
                        and appearance < hit <= event.video_time_ms
                        and response == hit - appearance
                    ):
                        self._response_totals[event.player] += response
                        player["responseSamples"] += 1
                        player["averageResponseTimeMs"] = (
                            self._response_totals[event.player] / player["responseSamples"]
                        )
                    else:
                        self._error = "Live scoreboard: invalid response timing excluded from average."
                timestamp = event.video_time_ms
                if type(timestamp) is int and timestamp >= 0:
                    if not self._playback_clock:
                        self._video_time_ms = max(self._video_time_ms, timestamp)
                    if self._video_time_ms - 5000 < timestamp <= self._video_time_ms:
                        for _ in range(outcome_count):
                            insort(self._recent_hits[event.player], timestamp)
                    self._refresh_pace()
                else:
                    self._error = "Live scoreboard: hit lacks a valid video timestamp; excluded from hit pace."
            else:
                return
            if type(event.video_time_ms) is int and event.video_time_ms >= 0:
                player["lastVideoTimeMs"] = max(player["lastVideoTimeMs"] or 0, event.video_time_ms)
            self._state["revision"] += 1
            self._publish_wakeup.set()

    def start(self, session_id: str):
        with self._lock:
            if self._state["active"]:
                return
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("The scoreboard publisher is still stopping. Try again shortly.")
            self._reset(session_id)
            self._state["active"] = True
            self._error = ""
            self._connection_status = "Live scoreboard: connecting - complete Microsoft sign-in if prompted"
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="scoreboard-publisher", daemon=True)
        self._thread.start()

    def _run(self):
        sender = None
        try:
            sender = self._sender_factory()
            while not self._stop.is_set():
                state = self.snapshot()
                try:
                    sender.publish(state)
                except ScoreboardConnectionError:
                    raise
                except ScoreboardPublishError as exc:
                    with self._lock:
                        self._connection_status = (
                            "Live scoreboard: update not acknowledged; retrying latest state. "
                            + (str(exc) if isinstance(exc, ScoreboardRegistrationError)
                               else "Check connectivity and publishing permissions.")
                        )
                    self._stop.wait(1)
                else:
                    with self._lock:
                        self._connection_status = (
                            f"Live scoreboard: connected | last acknowledged revision: {state['revision']}"
                        )
                        # Clear only when this acknowledgment includes every change.
                        # Updates arriving during the request keep the publisher awake.
                        if self._state["revision"] == state["revision"]:
                            self._publish_wakeup.clear()
                    self._publish_wakeup.wait(1)
            sender.publish(self.snapshot())
            with self._lock:
                self._connection_status = "Live scoreboard: off - shared scores cleared"
        except (ScoreboardPublishError, OSError, ValueError) as exc:
            with self._lock:
                self._error = (
                    f"Live scoreboard: {exc}" if isinstance(exc, ScoreboardPublishError)
                    else "Live scoreboard: publisher unavailable. Check Node.js and the scoreboard installation."
                )
                self._state["active"] = False
        finally:
            if sender is not None:
                try:
                    sender.close()
                except OSError:
                    with self._lock:
                        self._error = "Live scoreboard: failed to close the SDK worker cleanly."

    def stop(self, timeout: float = 1):
        with self._lock:
            self._state["active"] = False
            self._state["players"] = {"P1": _player(), "P2": _player()}
            self._state["revision"] += 1
            if self._thread is not None and self._thread.is_alive():
                self._connection_status = "Live scoreboard: stopping - clearing shared scores"
        self._stop.set()
        self._publish_wakeup.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if not self._thread.is_alive():
                self._thread = None

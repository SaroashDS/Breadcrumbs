from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import data_dir
from .session_names import generate_name, normalize_name


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            root = data_dir()
            current, legacy = root / "breadcrumbs.db", root / "tlock.db"
            path = legacy if not current.exists() and legacy.exists() else current
        self.path = path
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timer_kind TEXT NOT NULL,
                planned_seconds INTEGER NOT NULL CHECK (planned_seconds > 0),
                elapsed_seconds INTEGER NOT NULL DEFAULT 0,
                goal TEXT NOT NULL DEFAULT '',
                actual TEXT NOT NULL DEFAULT '',
                rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'abandoned'))
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_started_at
                ON sessions(started_at DESC);
            CREATE TABLE IF NOT EXISTS drafts (
                name TEXT PRIMARY KEY,
                content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_status
                ON sessions(status);
            CREATE VIEW IF NOT EXISTS completed_activity AS
                SELECT id, timer_kind, planned_seconds, elapsed_seconds, goal,
                       actual, rating, started_at, ended_at
                FROM sessions WHERE status = 'completed';
            """
        )
        self.connection.commit()
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(sessions)")}
        if "name" not in columns:
            self.connection.execute("ALTER TABLE sessions ADD COLUMN name TEXT")
        if "completion_reason" not in columns:
            self.connection.execute(
                "ALTER TABLE sessions ADD COLUMN completion_reason TEXT NOT NULL DEFAULT 'timer'"
            )
        if "session_root" not in columns:
            self.connection.execute("ALTER TABLE sessions ADD COLUMN session_root INTEGER REFERENCES sessions(id)")
        self.connection.execute("UPDATE sessions SET session_root = id WHERE session_root IS NULL")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_sessions_root ON sessions(session_root)")
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_name ON sessions(name)"
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS checkpoints (
                session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                remaining_seconds REAL NOT NULL CHECK (remaining_seconds >= 0),
                timer_status TEXT NOT NULL,
                saved_at TEXT NOT NULL
            )"""
        )
        self.connection.executescript(
            """CREATE TABLE IF NOT EXISTS session_spans (
                   id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
                   started_at REAL NOT NULL, ended_at REAL, last_seen REAL NOT NULL,
                   scope_snapshot TEXT NOT NULL DEFAULT '{}'
               );
               CREATE INDEX IF NOT EXISTS idx_spans_session ON session_spans(session_id);
               CREATE TABLE IF NOT EXISTS activity_intervals (
                   id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
                   span_id INTEGER NOT NULL REFERENCES session_spans(id),
                   started_at REAL NOT NULL, ended_at REAL NOT NULL,
                   app TEXT NOT NULL, title TEXT NOT NULL, domain TEXT NOT NULL,
                   context_key TEXT NOT NULL, source TEXT NOT NULL, precision TEXT NOT NULL,
                   classification TEXT NOT NULL, annotation TEXT NOT NULL DEFAULT ''
               );
               CREATE INDEX IF NOT EXISTS idx_activity_session ON activity_intervals(session_id, started_at);
               CREATE TABLE IF NOT EXISTS focus_scopes (
                   session_id INTEGER PRIMARY KEY REFERENCES sessions(id),
                   snapshot TEXT NOT NULL
               );
               CREATE TABLE IF NOT EXISTS pomodoro_state (
                   id INTEGER PRIMARY KEY CHECK (id = 1),
                   completed INTEGER NOT NULL DEFAULT 0
               );"""
        )
        span_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(session_spans)")}
        if "scope_snapshot" not in span_columns:
            self.connection.execute("ALTER TABLE session_spans ADD COLUMN scope_snapshot TEXT NOT NULL DEFAULT '{}'")
        activity_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(activity_intervals)")}
        for name in ("page_key", "agent_id", "notice"):
            if name not in activity_columns:
                self.connection.execute(f"ALTER TABLE activity_intervals ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        self.connection.execute(
            """INSERT OR IGNORE INTO pomodoro_state(id, completed)
               SELECT 1, COUNT(*) FROM sessions WHERE status = 'completed' AND timer_kind = 'focus'
               AND completion_reason = 'timer' AND ended_at > COALESCE(
                   (SELECT MAX(ended_at) FROM sessions WHERE status = 'completed'
                    AND timer_kind = 'long_break' AND completion_reason = 'timer'), '')"""
        )
        self.connection.commit()

    def begin_session(self, timer_kind: str, planned_seconds: int, goal: str, name: str | None = None,
                      session_root: int | None = None) -> int:
        if session_root is not None:
            root = self.connection.execute("SELECT id FROM sessions WHERE id = ? AND session_root = id", (session_root,)).fetchone()
            if root is None:
                raise ValueError("Unknown session")
            name = self.new_session_name()  # Internal block recovery identity; session name stays on the root.
        else:
            name = normalize_name(name) if name else self.new_session_name()
        cursor = self.connection.execute(
            """INSERT INTO sessions
               (timer_kind, planned_seconds, goal, started_at, name, status)
               VALUES (?, ?, ?, ?, ?, 'running')""",
            (timer_kind, planned_seconds, goal.strip(), utc_now(), name),
        )
        self.connection.execute(
            "INSERT INTO checkpoints VALUES (?, ?, 'running', ?)",
            (cursor.lastrowid, planned_seconds, utc_now()),
        )
        self.connection.execute("UPDATE sessions SET session_root = ? WHERE id = ?",
                                (session_root or cursor.lastrowid, cursor.lastrowid))
        self.connection.commit()
        return int(cursor.lastrowid)

    def session_identity(self, block_id: int) -> tuple[int, str]:
        row = self.connection.execute(
            "SELECT root.id, root.name FROM sessions block JOIN sessions root ON root.id = block.session_root WHERE block.id = ?",
            (block_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Unknown session")
        return row[0], row[1] or f"block-{row[0]}"

    def remember_session(self, root_id: int | None) -> None:
        self.connection.execute(
            "INSERT INTO drafts VALUES ('current-session', ?) ON CONFLICT(name) DO UPDATE SET content = excluded.content",
            (str(root_id) if root_id is not None else "",),
        )
        self.connection.commit()

    def current_session(self) -> tuple[int, str] | None:
        row = self.connection.execute("SELECT content FROM drafts WHERE name = 'current-session'").fetchone()
        if row and row[0].isdigit():
            try:
                return self.session_identity(int(row[0]))
            except ValueError:
                pass
        return None

    def new_session_name(self) -> str:
        while True:
            name = generate_name()
            if not self.connection.execute("SELECT 1 FROM sessions WHERE name = ?", (name,)).fetchone():
                return name

    def name_exists(self, name: str) -> bool:
        return self.connection.execute("SELECT 1 FROM sessions WHERE name = ?", (name,)).fetchone() is not None

    def checkpoint(self, session_id: int, remaining: float, timer_status: str, goal: str) -> None:
        with self.connection:
            row = self.connection.execute(
                "SELECT planned_seconds FROM sessions WHERE id = ? AND status = 'running'", (session_id,)
            ).fetchone()
            if row is None:
                return
            remaining = min(float(row[0]), max(0.0, remaining))
            elapsed = max(0, int(row[0] - remaining))
            self.connection.execute(
                """INSERT INTO checkpoints VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                   remaining_seconds = excluded.remaining_seconds,
                   timer_status = excluded.timer_status, saved_at = excluded.saved_at""",
                (session_id, remaining, timer_status, utc_now()),
            )
            self.connection.execute(
                "UPDATE sessions SET elapsed_seconds = ?, goal = ? WHERE id = ?",
                (elapsed, goal.strip(), session_id),
            )
            self.connection.execute(
                "UPDATE session_spans SET last_seen = MAX(last_seen, ?) WHERE session_id = ? AND ended_at IS NULL",
                (time.time(), session_id),
            )

    def close_spans(self, session_id: int, at: float | None = None) -> None:
        """With no at, seal crash leftovers at their last durable boundary."""
        with self.connection:
            rows = self.connection.execute(
                "SELECT * FROM session_spans WHERE session_id = ? AND ended_at IS NULL", (session_id,)
            ).fetchall()
            for row in rows:
                end = max(row["started_at"], row["last_seen"] if at is None else at)
                self.connection.execute("UPDATE session_spans SET ended_at = ? WHERE id = ?", (end, row["id"]))
                latest = self.connection.execute(
                    "SELECT id, ended_at FROM activity_intervals WHERE span_id = ? ORDER BY id DESC LIMIT 1", (row["id"],)
                ).fetchone()
                if latest is not None:
                    # A fresh final observation can cover at most one polling interval.
                    end_activity = min(end, latest["ended_at"] + (1.25 if at is not None else 0))
                    self.connection.execute(
                        "UPDATE activity_intervals SET ended_at = MAX(started_at, ?) WHERE id = ?",
                        (end_activity, latest["id"]),
                    )

    def open_span(self, session_id: int, at: float | None = None) -> int:
        self.close_spans(session_id)
        at = time.time() if at is None else at
        cursor = self.connection.execute(
            "INSERT INTO session_spans(session_id, started_at, last_seen) VALUES (?, ?, ?)",
            (session_id, at, at),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def save_scope(self, session_id: int, scope: dict) -> None:
        self.connection.execute(
            """INSERT INTO focus_scopes VALUES (?, ?)
               ON CONFLICT(session_id) DO UPDATE SET snapshot = excluded.snapshot""",
            (session_id, json.dumps(scope)),
        )
        self.connection.execute(
            "UPDATE session_spans SET scope_snapshot = ? WHERE session_id = ? AND ended_at IS NULL",
            (json.dumps(scope), session_id),
        )
        self.connection.commit()

    def load_scope(self, session_id: int) -> dict | None:
        row = self.connection.execute("SELECT snapshot FROM focus_scopes WHERE session_id = ?", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def span_scope(self, span_id: int) -> dict:
        row = self.connection.execute(
            "SELECT scope_snapshot FROM session_spans WHERE id = ?", (span_id,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def record_activity(self, session_id: int, span_id: int, context, classification: str,
                        at: float | None = None) -> int | None:
        at = time.time() if at is None else at
        with self.connection:
            span = self.connection.execute(
                "SELECT * FROM session_spans WHERE id = ? AND session_id = ? AND ended_at IS NULL",
                (span_id, session_id),
            ).fetchone()
            if span is None or at < span["started_at"]:
                return None
            previous = self.connection.execute(
                "SELECT * FROM activity_intervals WHERE span_id = ? ORDER BY id DESC LIMIT 1", (span_id,)
            ).fetchone()
            if previous is not None:
                gap = at - previous["ended_at"]
                if gap < 0:
                    return None  # wall-clock discontinuity; never create a negative duration
                if gap <= 3:
                    self.connection.execute("UPDATE activity_intervals SET ended_at = ? WHERE id = ?", (at, previous["id"]))
                    if (previous["context_key"] == context.key and previous["classification"] == classification
                            and previous["notice"] == context.notice):
                        self.connection.execute("UPDATE session_spans SET last_seen = MAX(last_seen, ?) WHERE id = ?", (at, span_id))
                        return previous["id"]
                elif gap > 3:
                    self.connection.execute(
                        """INSERT INTO activity_intervals
                           (session_id, span_id, started_at, ended_at, app, title, domain,
                            context_key, source, precision, classification)
                           VALUES (?, ?, ?, ?, 'Capture gap', '', '', 'gap', 'unknown', 'unknown', 'unknown')""",
                        (session_id, span_id, previous["ended_at"], at),
                    )
            cursor = self.connection.execute(
                """INSERT INTO activity_intervals
                   (session_id, span_id, started_at, ended_at, app, title, domain,
                    context_key, source, precision, classification, page_key, agent_id, notice)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, span_id, at, at, context.app, context.title, context.domain,
                 context.key, context.source, context.precision, classification, context.page_key, context.agent_id, context.notice),
            )
            self.connection.execute("UPDATE session_spans SET last_seen = MAX(last_seen, ?) WHERE id = ?", (at, span_id))
            return int(cursor.lastrowid)

    def activity_for(self, session_id: int) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM activity_intervals WHERE session_id = ? ORDER BY id", (session_id,)
        )]

    def activity_tail(self, session_id: int, limit: int = 3) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM activity_intervals WHERE session_id = ? ORDER BY id DESC LIMIT ?", (session_id, limit)
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def session_trail(self, block_id: int) -> list[dict]:
        root, _name = self.session_identity(block_id)
        rows = [dict(row) for row in self.connection.execute(
            """SELECT activity_intervals.* FROM activity_intervals JOIN sessions
               ON sessions.id = activity_intervals.session_id WHERE sessions.session_root = ?""", (root,)
        )]
        for span in self.connection.execute(
            """SELECT session_spans.*, sessions.timer_kind FROM session_spans JOIN sessions
               ON sessions.id = session_spans.session_id WHERE sessions.session_root = ?
               AND (sessions.timer_kind IN ('short_break', 'long_break') OR NOT EXISTS
                   (SELECT 1 FROM activity_intervals WHERE span_id = session_spans.id))""", (root,)
        ):
            is_break = span["timer_kind"] in ("short_break", "long_break")
            rows.append(dict(id=f"block:{span['id']}", session_id=span["session_id"], span_id=span["id"],
                started_at=span["started_at"], ended_at=span["ended_at"] if span["ended_at"] is not None else span["last_seen"],
                app=span["timer_kind"].replace("_", " ").title(), title="Activity capture is off during breaks" if is_break else "No activity captured in this running span",
                domain="", source="timer", precision="block", classification="break" if is_break else "block", annotation="",
                page_key="", agent_id=""))
        return sorted(rows, key=lambda row: (row["started_at"], row["span_id"], str(row["id"])))

    def annotate_activity(self, interval_id: int, annotation: str) -> None:
        if annotation not in ("planned", "supporting", "unclassified"):
            raise ValueError("Unknown branch label")
        self.connection.execute("UPDATE activity_intervals SET annotation = ? WHERE id = ?", (annotation, interval_id))
        self.connection.commit()

    def activity_label(self, interval_id: int) -> str:
        row = self.connection.execute("SELECT annotation FROM activity_intervals WHERE id = ?", (interval_id,)).fetchone()
        return row[0] if row else ""

    def pomodoro_progress(self) -> int:
        row = self.connection.execute("SELECT completed FROM pomodoro_state WHERE id = 1").fetchone()
        return int(row[0])

    def recoverable(self, name: str | None = None) -> list[dict[str, Any]]:
        sql = """SELECT s.*, c.remaining_seconds, c.timer_status, c.saved_at
                 FROM sessions s JOIN checkpoints c ON s.id = c.session_id
                 WHERE s.status = 'running'"""
        parameters = ()
        if name is not None:
            sql += " AND (s.name = ? OR s.session_root IN (SELECT id FROM sessions WHERE name = ?))"
            parameters = (name, name)
        sql += " ORDER BY c.saved_at DESC, s.id DESC"
        rows = [dict(row) for row in self.connection.execute(sql, parameters)]
        for row in rows:
            row["block_name"] = row["name"]
            row["name"] = self.session_identity(row["id"])[1]
        return rows

    def load_draft(self) -> str:
        row = self.connection.execute(
            "SELECT content FROM drafts WHERE name = 'intentions'"
        ).fetchone()
        return row[0] if row else "- [ ] "

    def save_draft(self, content: str) -> None:
        self.connection.execute(
            "INSERT INTO drafts(name, content) VALUES ('intentions', ?) "
            "ON CONFLICT(name) DO UPDATE SET content = excluded.content", (content,)
        )
        self.connection.commit()

    def finish_session(self, session_id: int, elapsed_seconds: int, reason: str = "timer") -> None:
        self.close_spans(session_id, time.time())
        with self.connection:
            changed = self.connection.execute(
                """UPDATE sessions SET elapsed_seconds = ?, ended_at = ?, status = 'completed', completion_reason = ?
                   WHERE id = ? AND status = 'running'""",
                (elapsed_seconds, utc_now(), reason, session_id),
            ).rowcount
            self.connection.execute("DELETE FROM checkpoints WHERE session_id = ?", (session_id,))
            if changed and reason == "timer":
                kind = self.connection.execute("SELECT timer_kind FROM sessions WHERE id = ?", (session_id,)).fetchone()[0]
                if kind == "focus":
                    self.connection.execute("UPDATE pomodoro_state SET completed = completed + 1 WHERE id = 1")
                elif kind == "long_break":
                    self.connection.execute("UPDATE pomodoro_state SET completed = 0 WHERE id = 1")

    def abandon_session(self, session_id: int, elapsed_seconds: int) -> None:
        self.close_spans(session_id, time.time())
        self.connection.execute(
            """UPDATE sessions SET elapsed_seconds = ?, ended_at = ?, status = 'abandoned'
               WHERE id = ? AND status = 'running'""",
            (elapsed_seconds, utc_now(), session_id),
        )
        self.connection.execute("DELETE FROM checkpoints WHERE session_id = ?", (session_id,))
        self.connection.commit()

    def add_reflection(self, session_id: int, actual: str, rating: int | None) -> None:
        self.connection.execute(
            "UPDATE sessions SET actual = ?, rating = ? WHERE id = ?",
            (actual.strip(), rating, session_id),
        )
        self.connection.commit()

    def update_goal(self, session_id: int, goal: str) -> None:
        self.connection.execute(
            "UPDATE sessions SET goal = ? WHERE id = ? AND status = 'running'",
            (goal.strip(), session_id),
        )
        self.connection.commit()

    def recent(self, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row["session_name"] = self.session_identity(row["id"])[1]
        return result

    def named_sessions(self, limit: int = 100) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            """SELECT root.* FROM sessions root JOIN sessions block ON block.session_root = root.id
               GROUP BY root.id ORDER BY MAX(block.id) DESC LIMIT ?""", (limit,)
        )]

    def stats(self) -> dict[str, int]:
        row = self.connection.execute(
            """SELECT
                COUNT(*) AS completed,
                COALESCE(SUM(elapsed_seconds), 0) AS elapsed_seconds,
                COALESCE(SUM(CASE WHEN timer_kind IN ('focus', 'custom') THEN 1 ELSE 0 END), 0)
                    AS completed_focuses
               FROM sessions WHERE status = 'completed'"""
        ).fetchone()
        return {key: int(row[key]) for key in row.keys()}

    def today_summary(self) -> dict[str, int]:
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight.astimezone(timezone.utc).isoformat(timespec="seconds")
        row = self.connection.execute(
            """SELECT COALESCE(SUM(elapsed_seconds), 0) AS seconds,
                      COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) AS blocks
               FROM sessions WHERE started_at >= ? AND timer_kind IN ('focus', 'custom')""",
            (start,),
        ).fetchone()
        return {"seconds": int(row["seconds"]), "blocks": int(row["blocks"])}

    def week_bounds(self, anchor: date, tz: timezone | None = None) -> tuple[datetime, datetime]:
        """Return the local-time Monday 00:00 and next Monday 00:00 around anchor.

        ``tz`` defaults to the machine's local timezone; pass an explicit
        timezone (e.g. a DST-observing one) for deterministic boundary checks.
        """
        local_monday = anchor - timedelta(days=anchor.weekday())
        start = datetime.combine(local_monday, datetime.min.time())
        end = datetime.combine(local_monday + timedelta(days=7), datetime.min.time())
        if tz is None:
            return start.astimezone(), end.astimezone()
        return start.replace(tzinfo=tz), end.replace(tzinfo=tz)

    def weekly_review(self, anchor: date, include_activity: bool = False, tz: timezone | None = None) -> dict[str, Any]:
        start, end = self.week_bounds(anchor, tz)
        start_iso = start.astimezone(timezone.utc).isoformat(timespec="seconds")
        end_iso = end.astimezone(timezone.utc).isoformat(timespec="seconds")
        rows = [dict(row) for row in self.connection.execute(
            """SELECT * FROM sessions
               WHERE started_at >= ? AND started_at < ?
               ORDER BY started_at ASC, id ASC""",
            (start_iso, end_iso),
        ).fetchall()]
        work = [row for row in rows if row["timer_kind"] in ("focus", "custom")]
        completed = [row for row in work if row["status"] == "completed"]
        early = [row for row in completed if row.get("completion_reason") == "early"]
        sessions: dict[int, dict[str, Any]] = {}
        for row in work:
            identity = self.session_identity(row["id"])
            entry = sessions.setdefault(identity[0], {
                "name": identity[1],
                "blocks": 0,
                "completed": 0,
                "seconds": 0,
            })
            entry["blocks"] += 1
            if row["status"] == "completed":
                entry["completed"] += 1
                entry["seconds"] += int(row["elapsed_seconds"])
        summary: dict[str, Any] = {
            "week_start": start.isoformat(timespec="minutes"),
            "week_end": end.isoformat(timespec="minutes"),
            "seconds": sum(int(row["elapsed_seconds"]) for row in completed),
            "blocks": len(work),
            "completed": len(completed),
            "early_finished": len(early),
            "abandoned": len([row for row in work if row["status"] == "abandoned"]),
            "sessions": list(sessions.values()),
        }
        if include_activity:
            labels: dict[str, int] = {}
            # Break work never contributes activity seconds.
            for row in work:
                for interval in self.activity_for(row["id"]):
                    label = (interval.get("annotation") or "").strip() or interval.get("classification") or "unlabelled"
                    labels[label] = labels.get(label, 0) + int(round(interval["ended_at"] - interval["started_at"]))
            summary["activity_seconds"] = labels
        return summary

    def export(self, limit: int | None = None, include_activity: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sessions ORDER BY started_at DESC"
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = [dict(row) for row in self.connection.execute(sql, params).fetchall()]
        for row in rows:
            row["session_name"] = self.session_identity(row["id"])[1]
        if include_activity:
            for row in rows:
                row["activity_schema_version"] = 1
                row["activity_intervals"] = self.activity_for(row["id"])
                row["focus_scope"] = self.load_scope(row["id"])
                row["running_spans"] = [dict(span) for span in self.connection.execute(
                    "SELECT * FROM session_spans WHERE session_id = ? ORDER BY id", (row["id"],)
                )]
                for span in row["running_spans"]:
                    span["scope_snapshot"] = json.loads(span["scope_snapshot"])
        return rows

    def export_json(self, limit: int | None = None, include_activity: bool = False) -> str:
        return json.dumps(self.export(limit, include_activity), indent=2, ensure_ascii=False)

    CSV_SESSION_COLUMNS = (
        "id",
        "session_name",
        "timer_kind",
        "planned_seconds",
        "elapsed_seconds",
        "status",
        "completion_reason",
        "started_at",
        "ended_at",
        "goal",
        "actual",
        "rating",
    )

    def export_csv(self, limit: int | None = None) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=self.CSV_SESSION_COLUMNS,
            restval="",
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in self.export(limit):
            writer.writerow(row)
        return buffer.getvalue()

    def close(self) -> None:
        self.connection.close()

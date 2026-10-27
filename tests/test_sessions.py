import asyncio
import sqlite3
import subprocess
import sys
import time

import pytest
from textual.widgets import Select, Static

from breadcrumbs.app import BreadcrumbsApp
from breadcrumbs.config import Settings
from breadcrumbs.db import Database
from breadcrumbs.reading import QUOTES, next_card
from breadcrumbs.screens import RecoveryScreen, SessionDecisionScreen
from breadcrumbs.session_names import generate_name, normalize_name
from breadcrumbs.timer import TimerKind, TimerStatus
from breadcrumbs.widgets import GoalEditor, ReadingCard


def test_checkpoint_survives_connection_restart_and_early_finish(tmp_path):
    path = tmp_path / "history.db"
    db = Database(path)
    session_id = db.begin_session("custom", 3600, "Plan", "bright-otter")
    db.checkpoint(session_id, 3123.5, "running", "- [x] First\n- [ ] Next")
    db.close()
    db = Database(path)
    saved = db.recoverable("bright-otter")[0]
    assert saved["id"] == session_id
    assert saved["remaining_seconds"] == 3123.5
    assert saved["elapsed_seconds"] == 476
    assert "Next" in saved["goal"]
    db.finish_session(session_id, 476, "early")
    assert db.recoverable() == []
    row = db.recent(1)[0]
    assert row["status"] == "completed"
    assert row["completion_reason"] == "early"
    assert row["elapsed_seconds"] == 476
    db.close()


def test_checkpoint_survives_abrupt_process_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))
    subprocess.run(
        [sys.executable, "-c",
         "import os; from breadcrumbs.db import Database; db=Database(); "
         "sid=db.begin_session('focus', 1500, 'Crash safe', 'steady-willow'); "
         "db.checkpoint(sid, 1234.5, 'running', 'Crash safe'); os._exit(0)"],
        check=True,
    )
    db = Database()
    saved = db.recoverable("steady-willow")[0]
    assert saved["remaining_seconds"] == 1234.5
    assert saved["goal"] == "Crash safe"
    db.close()


def test_expiry_waits_for_settings_and_custom_timer_stays_standalone(tmp_path, monkeypatch):
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))

    async def exercise():
        app = BreadcrumbsApp()
        app.settings.sound_enabled = False
        app.settings.auto_start_breaks = True
        async with app.run_test(size=(110, 40)) as pilot:
            app._select(TimerKind.CUSTOM, 1)
            app._begin_block()
            app.action_settings()
            await pilot.pause()
            app.screen.query_one("#reading-mode", Select).value = "quotes"
            app.timer._deadline = time.monotonic() - 1
            app._tick()
            assert app.active_session_id is not None
            assert app.screen.query_one("#reading-mode", Select).value == "quotes"
            await pilot.press("ctrl+s")
            await pilot.pause()
            app._tick()
            await pilot.pause()
            assert app.active_session_id is None
            assert app.settings.effective_reading_mode == "quotes"
            await pilot.press("escape")
            await pilot.pause()
            assert app.timer.kind is TimerKind.CUSTOM
            assert app.timer.status is TimerStatus.READY
            assert app.timer.duration_seconds == 1

    asyncio.run(exercise())


def test_migration_preserves_legacy_history(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE sessions (
        id INTEGER PRIMARY KEY, timer_kind TEXT, planned_seconds INTEGER,
        elapsed_seconds INTEGER, goal TEXT, actual TEXT, rating INTEGER,
        started_at TEXT, ended_at TEXT, status TEXT)""")
    db.execute("""INSERT INTO sessions VALUES
        (1, 'focus', 1500, 1500, 'Old plan', 'Old reflection', 4,
         '2025-01-01T10:00:00+00:00', '2025-01-01T10:25:00+00:00', 'completed')""")
    db.commit()
    db.close()
    upgraded = Database(path)
    assert upgraded.recent()[0]["actual"] == "Old reflection"
    assert upgraded.recent()[0]["completion_reason"] == "timer"
    upgraded.begin_session("focus", 1500, "New plan", "calm-fern")
    assert len(upgraded.recoverable()) == 1
    upgraded.close()


def test_quit_and_resume_reuse_the_same_session(tmp_path, monkeypatch):
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))

    async def exercise():
        app = BreadcrumbsApp(session_name="quiet-otter")
        async with app.run_test(size=(110, 40)) as pilot:
            app._home_query("#goal-input", GoalEditor).load_text("- [ ] Recover me")
            await pilot.pause()
            app._begin_block()
            session_id = app.active_session_id
            app.timer.pause()
            app.timer.remaining_seconds = 1312.5
        assert app.resume_hint == "quiet-otter"

        restored = BreadcrumbsApp(resume_name="quiet-otter")
        async with restored.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            assert restored.timer.status is TimerStatus.PAUSED
            assert restored.timer.remaining_seconds == 1312.5
            assert restored.active_session_id == session_id
            assert restored._home_query("#goal-input", GoalEditor).goal_text == "- [ ] Recover me"
            assert len(restored.database.export()) == 1
            restored.action_toggle()
            assert restored.timer.status is TimerStatus.RUNNING

    asyncio.run(exercise())


def test_switch_cancel_finish_and_end_preserve_time(tmp_path, monkeypatch):
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))

    async def exercise():
        app = BreadcrumbsApp(session_name="bright-fern")
        app.settings.sound_enabled = False
        async with app.run_test(size=(110, 40)) as pilot:
            app._begin_block()
            first_id = app.active_session_id
            app.action_short_break()
            await pilot.pause()
            assert isinstance(app.screen, SessionDecisionScreen)
            assert app.timer.kind is TimerKind.FOCUS
            await pilot.press("escape")
            await pilot.pause()
            assert app.timer.status is TimerStatus.RUNNING
            assert app.active_session_id == first_id
            app.timer.pause()
            app.timer.remaining_seconds = 1400
            app._preset_chosen((50, 10))
            await pilot.pause()
            assert app.settings.focus_minutes == 25
            await pilot.click("#finish-early")
            await pilot.pause()
            row = app.database.recent(1)[0]
            assert row["elapsed_seconds"] == 100
            assert row["status"] == "completed"
            assert row["completion_reason"] == "early"
            assert app.database.recoverable() == []
            await pilot.press("escape")  # skip optional reflection
            await pilot.pause()
            assert app.settings.focus_minutes == 50
            assert app.timer.duration_seconds == 3000
            assert app.timer.kind is TimerKind.FOCUS
            app._begin_block()
            app.timer.pause()
            app.timer.remaining_seconds = 2900
            app.action_reset()
            await pilot.pause()
            await pilot.click("#end-session")
            await pilot.pause()
            assert app.timer.status is TimerStatus.READY
            assert app.database.recent(1)[0]["status"] == "abandoned"
            assert app.database.recent(1)[0]["elapsed_seconds"] == 100
            assert app.database.recoverable() == []
            assert len(app.database.export()) == 2

    asyncio.run(exercise())


def test_new_session_keeps_old_recovery_and_cards_rotate(tmp_path, monkeypatch):
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))
    db = Database()
    old = db.begin_session("focus", 1500, "Saved", "merry-cloud")
    db.checkpoint(old, 1200, "running", "Saved")
    db.close()

    async def exercise():
        app = BreadcrumbsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, RecoveryScreen)
            app._tick()  # main widgets remain queryable while modal is open
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.database.recoverable()) == 1
            app.settings.reading_mode = "quotes"
            app._reading_due = 0
            app._refresh_view()
            card = app._home_query("#verse", ReadingCard)
            first = str(card.query_one("#reading-text", Static).renderable)
            assert "original reminder" in first
            app._reading_due = 0
            app._refresh_view()
            assert str(card.query_one("#reading-text", Static).renderable) != first
            app.settings.reading_mode = "quran"
            app._refresh_view()
            assert "English meaning" in str(card.query_one("#reading-text", Static).renderable)
            app.settings.reading_mode = "off"
            app._refresh_view()
            assert not card.has_class("visible")
            app._begin_block()
            assert app.active_session_id != old
            assert len(app.database.recoverable()) == 2

    asyncio.run(exercise())


def test_session_names_and_reading_compatibility():
    for _ in range(20):
        name = generate_name()
        assert normalize_name(name) == name
    assert normalize_name("My Work") == "my-work"
    with pytest.raises(ValueError):
        normalize_name("../bad")
    assert Settings(quran_enabled=False).effective_reading_mode == "off"
    assert Settings(reading_mode="quotes").effective_reading_mode == "quotes"
    index, text = next_card("quotes")
    assert QUOTES[index] in text
    assert next_card("quotes", index)[0] != index


@pytest.mark.parametrize(
    ("input_name", "expected"),
    [
        ("a", "a"),
        ("9", "9"),
        ("a" * 60, "a" * 60),
        ("a" + "-" * 58 + "z", "a" + "-" * 58 + "z"),
        ("My Work", "my-work"),
        ("deep_focus", "deep-focus"),
        ("multiple   spaces", "multiple-spaces"),
        ("multiple___underscores", "multiple-underscores"),
        ("mixed _ separators", "mixed-separators"),
        ("  padded  ", "padded"),
        ("UPPERCASE", "uppercase"),
    ],
)
def test_normalize_name_valid_boundaries_and_normalization(input_name: str, expected: str):
    assert normalize_name(input_name) == expected


@pytest.mark.parametrize(
    "invalid_name",
    [
        "",
        "   ",
        "\t\n",
        "a" * 61,
        "-leading-hyphen",
        "trailing-hyphen-",
        "-both-ends-",
        "-",
        "---",
        "_leading_underscore",
        "trailing_underscore_",
        "../bad",
        "session.name",
        "bad@symbol",
        "has!mark",
        "slash/path",
        "hash#tag",
    ],
)
def test_normalize_name_invalid_boundaries_raise_actionable_error(invalid_name: str):
    with pytest.raises(
        ValueError,
        match="Use 1–60 letters, numbers, or hyphens; start and end with a letter or number.",
    ) as exc_info:
        normalize_name(invalid_name)
    assert str(exc_info.value) == "Use 1–60 letters, numbers, or hyphens; start and end with a letter or number."



def test_daily_summary_excludes_breaks_and_old_sessions(tmp_path):
    db = Database(tmp_path / "history.db")
    focus = db.begin_session("focus", 1500, "")
    db.finish_session(focus, 90, "early")
    partial = db.begin_session("custom", 600, "")
    db.checkpoint(partial, 400, "paused", "")
    rest = db.begin_session("short_break", 300, "")
    db.finish_session(rest, 300)
    old = db.begin_session("focus", 1500, "")
    db.finish_session(old, 1500)
    db.connection.execute("UPDATE sessions SET started_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (old,))
    db.connection.commit()
    assert db.today_summary() == {"seconds": 290, "blocks": 1}
    db.close()


def test_resume_cli_alias_and_naming_flags():
    from breadcrumbs.__main__ import build_parser

    parser = build_parser()
    assert parser.parse_args(["--resume", "calm-otter"]).resume == "calm-otter"
    assert parser.parse_args(["--session", "calm-otter"]).resume == "calm-otter"
    assert parser.parse_args(["sessions"]).command == "sessions"
    with pytest.raises(SystemExit):
        parser.parse_args(["--resume", "one", "--name", "two"])


def test_export_and_browser_help_explain_defaults_and_examples():
    from breadcrumbs.__main__ import build_parser

    parser = build_parser()
    actions = {action.dest: action for action in parser._subparsers._group_actions}
    export = actions["command"].choices["export"]
    browser = actions["command"].choices["browser"]

    export_help = export.format_help()
    assert "stdout" in export_help
    assert "session summaries" in export_help
    assert "--activity" in export_help
    assert "breadcrumbs export --limit 20" in export_help
    assert "breadcrumbs export --format csv" in export_help

    browser_help = browser.format_help()
    assert "path prints the extension folder" in browser_help
    assert "repair reinstalls it" in browser_help
    assert "breadcrumbs browser setup" in browser_help

    # The flags and their defaults keep working as before.
    parsed = parser.parse_args(["export", "--limit", "5", "--output", "out.json"])
    assert parsed.limit == 5
    assert str(parsed.output) == "out.json"
    assert parsed.format == "json"
    assert parser.parse_args(["export"]).limit is None
    assert parser.parse_args(["browser"]).action == "setup"


def test_previous_two_column_layout_and_labeled_name_field(tmp_path, monkeypatch):
    from textual.widgets import Input

    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))

    async def exercise():
        app = BreadcrumbsApp()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            assert not app.screen.has_class("compact")
            timer = app.query_one("#timer-panel")
            intentions = app.query_one("#companion-panel")
            assert timer.region.y == intentions.region.y
            assert intentions.region.x > timer.region.x
            name = intentions.query_one("#session-name", Input)
            assert str(name.border_title) == "Session name"
            assert name.placeholder == "Session name"
            assert not len(timer.query("#session-name"))

    asyncio.run(exercise())

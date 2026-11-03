from __future__ import annotations

import sqlite3
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from rich.text import Text

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, ScrollableContainer, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Static, TextArea

from .config import Settings
from .activity import Context, activity_label, classify, create_source, suggested_apps, UnavailableSource
from .branches import BranchesScreen, ScopeScreen, StartSessionScreen, PairBrowserScreen
from .bridge import BridgeServer
from .brands import activity_brand, brand_for, render_logo
from .db import Database
from .mascot import PixelMascot
from .screens import HistoryScreen, PresetScreen, RecoveryScreen, ReflectionScreen, SessionDecisionScreen, SettingsScreen
from .session_names import normalize_name
from .sound import play_completion
from .timer import TimerEngine, TimerKind, TimerStatus
from .reading import next_card
from .onboarding import OnboardingScreen, TOUR_VERSION
from .widgets import CommandBar, GoalEditor, PixelProgress, ReadingCard, MiniTrail
from .branding import BreadcrumbsMark


def _clock_text(seconds: float) -> str:
    """mm:ss (or h:mm:ss) text matching the timer clock format."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


class BreadcrumbsApp(App[None]):
    TITLE = "Breadcrumbs"
    CSS = """
    $bg: #090b12;
    $panel: #131827;
    $gold: #ffd166;
    $mint: #70e1c1;
    $pink: #ff6b9d;
    $dim: #78869f;

    Screen { background: $bg; color: #edf2f7; }
    #brand { height: 3; padding: 0 3; background: transparent; }
    #brand-name { width: 1fr; content-align: left middle; text-style: bold; color: $mint; }
    #summary { width: 1fr; content-align: right middle; color: $dim; }
    #body { height: 1fr; padding: 0 2 1 2; layout: horizontal; }
    .panel { background: $panel; border: round #303a55; padding: 1 2; margin: 0 1; }
    #timer-panel { width: 40%; min-width: 37; }
    #companion-panel { width: 1fr; min-width: 30; }
    #mode { color: $mint; text-style: bold; text-align: center; margin-top: 1; }
    #session-name { height: 3; margin-bottom: 1; border: tall #303a55; border-title-color: $dim; }
    #session-name:disabled { opacity: 1; border-title-color: $dim; color: #aab4c8; }
    #clock { color: $gold; text-style: bold; text-align: center; height: 5;
        content-align: center middle; }
    #progress { margin: 0 1 1 1; color: $mint; height: 1; text-align: center; }
    #break-progress { height: 2; color: $dim; text-align: center; }
    #footer { dock: bottom; height: auto; }
    #footer CommandBar { dock: none; }
    #active-context { height: 1; background: #0d111d; color: #aab4c8; padding: 0 1; }
    #mode-buttons { grid-size: 3; grid-gutter: 1; height: 3; }
    #control-buttons { grid-size: 3; grid-gutter: 1; height: 3; margin-top: 1; }
    Button { background: #242b40; color: #dce5f5; border: none; text-style: bold; }
    Button:hover { background: #3b465d; color: $gold; }
    #start { background: #237561; }
    #reset { color: $pink; }
    #custom-row { height: 3; margin-top: 1; }
    #custom-minutes { width: 1fr; }
    #custom { width: 13; margin-left: 1; }
    #mascot { height: 5; color: $mint; text-align: center; content-align: center middle; }
    #mascot-line { text-align: center; color: #aab4c8; height: 1; margin-bottom: 1; }
    .pet-hidden #mascot, .pet-hidden #mascot-line { display: none; }
    #intention-workspace { height: 1fr; min-height: 11; }
    #goal-input { width: 1fr; height: 1fr; min-height: 9; background: #0d111d; border: tall #303a55; }
    #goal-input:focus { border: tall $mint; }
    #goal-help { height: 2; color: $dim; text-align: center; }
    #verse { display: none; height: auto; min-height: 7; margin-top: 1; padding: 1;
        border: heavy #53617a; color: #d8e2f0; text-align: center; background: #0d111d; }
    #verse.visible { display: block; }
    .modal-title { color: $gold; text-style: bold; text-align: center; height: 2; }
    .status-ready { color: #aab4c8; }
    .status-running { color: $mint; }
    .status-paused { color: #ffad5a; }
    .status-finished { color: $gold; }

    Screen.theme-gameboy { background: #11180d; color: #d8f3a6; }
    Screen.theme-gameboy .panel { background: #1c2a14; border: round #668c3c; }
    Screen.theme-gameboy #clock { color: #c7f464; }
    Screen.theme-gameboy #progress, Screen.theme-gameboy #mascot,
    Screen.theme-gameboy #mode { color: #8fd14f; }

    Screen.theme-sunset { background: #170d24; color: #ffe6d6; }
    Screen.theme-sunset .panel { background: #281438; border: round #7d477e; }
    Screen.theme-sunset #clock { color: #ffb45e; }
    Screen.theme-sunset #progress, Screen.theme-sunset #mascot,
    Screen.theme-sunset #mode { color: #ff75a8; }

    Screen.compact #body { layout: vertical; overflow-y: scroll; overflow-x: hidden; }
    Screen.compact #timer-panel, Screen.compact #companion-panel {
        width: 100%; min-width: 0; height: auto; margin-bottom: 1; }
    Screen.compact #timer-panel { min-height: 23; }
    Screen.compact #companion-panel { min-height: 17; }
    Screen.compact #intention-workspace { height: 12; }

    Screen.theme-gameboy #settings-card, Screen.theme-gameboy #reflection-card,
    Screen.theme-gameboy #history-card { background: #1c2a14; border: round #88b04b; }
    Screen.theme-sunset #settings-card, Screen.theme-sunset #reflection-card,
    Screen.theme-sunset #history-card { background: #281438; border: round #a84f8e; }
    Screen.theme-gameboy .session-card { background: #1c2a14; border: round #88b04b; }
    Screen.theme-sunset .session-card { background: #281438; border: round #a84f8e; }
    Screen.theme-gameboy #branches-card, Screen.theme-gameboy #scope-card { background: #1c2a14; }
    Screen.theme-sunset #branches-card, Screen.theme-sunset #scope-card { background: #281438; }

    """
    BINDINGS = [
        ("space", "toggle", "Start/Pause"),
        ("r", "reset", "Reset"),
        ("f", "finish", "Finish early"),
        ("s", "skip_break", "Skip break & bank"),
        ("p", "presets", "Presets"),
        ("m", "pet", "Pet"),
        ("n", "spawn_pet", "New pet"),
        ("c", "settings", "Config"),
        ("h", "history", "History"),
        ("b", "branches", "Focus branches"),
        ("e", "edit_goals", "Edit intentions"),
        ("escape", "leave_editor", "Back to timer"),
        ("1", "focus", "Focus"),
        ("2", "short_break", "Short break"),
        ("3", "long_break", "Long break"),
        ("q", "quit", "Quit"),
        ("question_mark", "tour", "Feature tour"),
        ("ctrl+n", "new_session", "New session"),
    ]

    def __init__(self, resume_name: str | None = None, session_name: str | None = None,
                 *, show_onboarding: bool = False) -> None:
        super().__init__()
        self.settings = Settings.load()
        self.database = Database()
        self._goal_text = self.database.load_draft()
        self.timer = TimerEngine(self.settings.focus_minutes * 60)
        self.active_session_id: int | None = None
        self.completed_focuses = self.database.stats()["completed_focuses"]
        self.pomodoro_done = self.database.pomodoro_progress()
        self.session_name = session_name or self.database.new_session_name()
        self._session_root = None
        remembered = self.database.current_session() if session_name is None and resume_name is None else None
        if remembered:
            self._session_root, self.session_name = remembered
        self.banked_seconds = self.database.break_bank(self._session_root)
        self._start_new_requested = session_name is not None
        self.resume_name = resume_name
        self.resume_hint: str | None = None
        self._last_checkpoint = 0.0
        self._decision_open = False
        self._completion_pending = False
        self._reading_index: int | None = None
        self._reading_mode: str | None = None
        self._reading_due = 0.0
        self._home_screen: Screen | None = None
        self._span_id: int | None = None
        self._activity_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="breadcrumbs-context")
        self._activity_future = None
        self._activity_generation = 0
        self._activity_next_poll = 0.0
        self._collector = None
        self._current_context: Context | None = None
        self._current_activity_id: int | None = None
        self._tracking_settings = replace(self.settings)
        self._start_flow_open = False
        self._bridge = None
        self._bridge_failed = False
        self._bridge_retry_after = 0.0
        self._agent_markers = None
        self._show_onboarding = show_onboarding

    def compose(self) -> ComposeResult:
        with Horizontal(id="brand"):
            yield BreadcrumbsMark(id="brand-mark")
            yield Static("BREADCRUMBS", id="brand-name")
            yield Static("", id="summary")
        with ScrollableContainer(id="body"):
            with Vertical(classes="panel", id="timer-panel"):
                yield Static("FOCUS RUN", id="mode")
                yield Static(self.timer.display, id="clock")
                yield PixelProgress(id="progress")
                yield Static("", id="break-progress", markup=False)
                with Grid(id="mode-buttons"):
                    yield Button("[1] FOCUS", id="focus")
                    yield Button("[2] SHORT", id="short-break")
                    yield Button("[3] LONG", id="long-break")
                with Horizontal(id="custom-row"):
                    yield Input(placeholder="custom minutes", id="custom-minutes", type="integer")
                    yield Button("SET TIMER", id="custom")
                with Grid(id="control-buttons"):
                    yield Button("▶ START", id="start")
                    yield Button("■ RESET", id="reset")
                    yield Button("◆ CONFIG", id="settings")
            with VerticalScroll(classes="panel", id="companion-panel"):
                yield Input(self.session_name, placeholder="Session name", id="session-name")
                yield PixelMascot(self.settings.pet_key, id="mascot")
                yield Static("Ready when you are.", id="mascot-line")
                with Horizontal(id="intention-workspace"):
                    yield GoalEditor(self.database.load_draft(), id="goal-input")
                    yield MiniTrail(id="mini-trail")
                yield Static("CTRL+B new task  ·  F2 tick/untick  ·  mouse wheel scrolls", id="goal-help")
                yield ReadingCard(id="verse")
        with Vertical(id="footer"):
            yield Static("", id="active-context", markup=False)
            yield CommandBar(
                "SPACE start/pause · B branches · E intentions · H history · C config · CTRL+N new session · ? tour · Q quit"
            )

    def on_mount(self) -> None:
        self._home_screen = self.screen
        self._apply_theme()
        self._apply_layout(self.size.width)
        self.set_interval(0.2, self._tick)
        self._refresh_view()
        self._refresh_history()
        self._home_query("#companion-panel", VerticalScroll).border_title = "Intentions"
        self._home_query("#timer-panel", Vertical).border_title = "Timer"
        self._home_query("#start", Button).focus()
        self._home_query("#session-name", Input).border_title = "Session name"
        self._home_query("#session-name", Input).tooltip = "Use your own name or keep the generated one."
        self._home_query("#start", Button).tooltip = "Space: start or pause. F: finish early."
        self._home_query("#focus", Button).tooltip = "P: choose a saved focus rhythm."
        from .bridge import pairing_file
        if pairing_file().exists():
            self._ensure_bridge()
            self._publish_bridge()
        self._offer_recovery()
        if (self._show_onboarding and self.settings.onboarding_version < TOUR_VERSION
                and self.screen is self._home_screen and self.active_session_id is None):
            self.action_tour()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self.screen_stack and isinstance(self.screen, OnboardingScreen):
            return action == "quit"
        return True

    def action_tour(self):
        if self.screen is not self._home_screen or self._completion_pending:
            return
        if self.timer.status is TimerStatus.RUNNING:
            self.notify("Pause with Space before opening the feature tour.")
            return
        self.push_screen(self._themed(OnboardingScreen()), self._tour_closed)
        self._publish_bridge()

    def _tour_closed(self, _result):
        self.settings.onboarding_version = TOUR_VERSION
        self.settings.save()
        self._home_query("#start", Button).focus()
        self._publish_bridge()


    def _themed(self, screen):
        screen.add_class(f"theme-{self.settings.ui_theme}")
        return screen

    def _offer_recovery(self) -> None:
        if self.active_session_id is not None:
            return
        if self.resume_name:
            self._recover(self.resume_name)
        elif not self._start_new_requested:
            rows = self.database.recoverable()
            if rows:
                self.push_screen(self._themed(RecoveryScreen(rows)), self._recover)

    def _recover(self, name: str | None) -> None:
        if name is None:
            # Recovery's "Start new" / Escape is an explicit new-session choice.
            if self._session_root is not None:
                self._session_root = None
                self.database.remember_session(None)
                self.session_name = self.database.new_session_name()
                self._home_query("#session-name", Input).value = self.session_name
                self._reload_break_bank()
            return
        rows = self.database.recoverable(name)
        if not rows:
            self.notify("No saved block with that name.", severity="error")
            return
        row = rows[0]
        self.database.close_spans(row["id"])
        scope = self.database.load_scope(row["id"])
        if scope:
            self._tracking_settings = replace(
                self.settings, activity_source=("integrated" if scope.get("source") in ("desktop", "activitywatch")
                                                else scope.get("source", "off")),
                activity_apps=scope.get("apps", []), activity_domains=scope.get("domains", []),
                activity_pages=scope.get("pages", []), activity_agents=scope.get("agents", []),
                activity_titles=scope.get("titles", False),
            )
        self.timer.configure(row["planned_seconds"], TimerKind(row["timer_kind"]))
        self.timer.remaining_seconds = row["remaining_seconds"]
        self.timer.status = TimerStatus.PAUSED
        self.active_session_id = row["id"]
        self._session_root, self.session_name = self.database.session_identity(row["id"])
        self.database.remember_session(self._session_root)
        self._reload_break_bank()
        self._goal_text = row["goal"]
        self._home_query("#session-name", Input).value = self.session_name
        self._home_query("#goal-input", GoalEditor).load_text(row["goal"] or GoalEditor.DEFAULT_TEXT)
        self._checkpoint()
        self._refresh_view()
        self.notify(f"Recovered {name}. Press Space to resume.", title="Session recovered")

    def on_resize(self, event: events.Resize) -> None:
        self._apply_layout(event.size.width)

    def _apply_layout(self, width: int) -> None:
        (self._home_screen or self.screen).set_class(width < 85 or self.size.height < 28, "compact")

    def _home_query(self, selector: str, expect_type):
        """Query the timer's home screen even while a modal is active."""
        return (self._home_screen or self.screen).query_one(selector, expect_type)

    def on_unmount(self) -> None:
        if self.active_session_id is not None:
            self.timer.pause()
            self._checkpoint()
            self.resume_hint = self.session_name
        self._activity_executor.shutdown(wait=False, cancel_futures=True)
        if self._bridge:
            self._bridge.state.publish(False, False)
        if self._agent_markers:
            self._agent_markers.close()
        if self._bridge:
            self._bridge.close()
        self.database.close()

    def _checkpoint(self) -> None:
        self._sync_span()
        if self.active_session_id is not None:
            self.database.checkpoint(
                self.active_session_id, self.timer.remaining_seconds,
                self.timer.status.value, self._goal_text,
            )
        self._last_checkpoint = time.monotonic()

    def _reload_break_bank(self) -> None:
        """Mirror the named session's banked break time from the database."""
        self.banked_seconds = self.database.break_bank(self._session_root)

    def _scope_snapshot(self):
        settings = self._tracking_settings
        return dict(source=settings.activity_source, apps=settings.activity_apps,
                    domains=settings.activity_domains, titles=settings.activity_titles,
                    pages=settings.activity_pages, agents=settings.activity_agents)

    def _ensure_bridge(self):
        if self._bridge is None and time.monotonic() >= self._bridge_retry_after:
            try:
                self._bridge = BridgeServer(self.settings.browser_bridge_port).start()
                self._bridge_failed = False
                from .terminal import AgentMarkers
                self._agent_markers = AgentMarkers(self._bridge)
            except (OSError, ValueError):
                first_failure = not self._bridge_failed
                self._bridge_failed = True
                self._bridge_retry_after = time.monotonic() + 5
                if first_failure:
                    self.notify("Browser connection unavailable. Close other Breadcrumbs instances using this port; connection will retry.", severity="warning")
        return self._bridge

    def _publish_bridge(self):
        settings = self._tracking_settings
        active = self._capture_wanted() and settings.activity_source == "integrated"
        inventory = any(isinstance(screen, (ScopeScreen,)) for screen in self.screen_stack)
        if active or self.active_session_id is not None:
            self._ensure_bridge()
        if self._bridge:
            self._bridge.state.publish(active, settings.activity_titles, inventory)
            self._bridge.state.publish_timer(dict(
                status=self.timer.status.value, session_id=self.active_session_id, name=self.session_name,
                remaining_seconds=max(0, int(self.timer.remaining_seconds)), kind=self.timer.kind.value,
                can_control=self._remote_controls_available(),
                ui_theme=self.settings.ui_theme,
            ))
        if self._agent_markers:
            self._agent_markers.sync(settings.activity_agents if active else [])

    def _remote_controls_available(self):
        return bool(self.screen_stack) and not self._completion_pending and (
            self.screen is self._home_screen or isinstance(self.screen, (BranchesScreen, HistoryScreen)))

    def _process_remote_controls(self):
        if not self._bridge:
            return
        for command in self._bridge.state.take_controls():
            if (not self._remote_controls_available()
                    or command["session_id"] != self.active_session_id
                    or command["status"] != self.timer.status.value):
                continue
            action = command["action"]
            if action in ("start", "finish") and self.screen is not self._home_screen:
                self.pop_screen()
            if action == "start":
                self._tracking_settings = replace(self.settings)
                self._collector = None
                self._begin_block()
            elif action in ("pause", "resume"):
                self._begin_block()
            elif action == "finish" and self.active_session_id is not None:
                kind, seconds = self.timer.kind, self.timer.duration_seconds
                self._finish_block(self.timer.elapsed_seconds, "early", lambda: self._configure(kind, seconds))
        self._publish_bridge()

    def _classify(self, context):
        settings = self._tracking_settings
        return classify(context, settings.activity_apps, settings.activity_domains,
                        settings.activity_pages, settings.activity_agents)

    def _sync_span(self):
        if self.active_session_id is not None and self.timer.status is TimerStatus.RUNNING:
            if self._span_id is None:
                self._span_id = self.database.open_span(self.active_session_id)
                self._current_context = None
                self._current_activity_id = None
                self.database.save_scope(self.active_session_id, self._scope_snapshot())
                self._activity_generation += 1
        else:
            self._stop_span()
        self._publish_bridge()

    def _stop_span(self):
        if self._span_id is not None:
            if self.active_session_id is not None:
                self.database.close_spans(self.active_session_id, time.time())
            self._span_id = None
            self._activity_generation += 1

    def _capture_wanted(self):
        return (self.active_session_id is not None and self._span_id is not None
                and self.timer.status is TimerStatus.RUNNING
                and self.timer.kind in (TimerKind.FOCUS, TimerKind.CUSTOM)
                and self._tracking_settings.activity_source != "off")

    def _poll_activity(self):
        if self._activity_future is not None:
            future, generation, session_id, span_id = self._activity_future
            if future.done():
                self._activity_future = None
                try:
                    context, observed_at = future.result()
                except Exception:
                    context = Context("Unknown", source=self._tracking_settings.activity_source,
                                      status="unknown", notice="Source unavailable · B → Scope / source")
                    observed_at = time.time()
                if (self._capture_wanted() and generation == self._activity_generation
                        and session_id == self.active_session_id and span_id == self._span_id):
                    self._current_context = context
                    self._current_activity_id = self.database.record_activity(session_id, span_id, context,
                        self._classify(context), observed_at)
                    if self._current_activity_id is None:
                        self._current_context = Context("Unknown", status="unknown",
                            notice="Clock boundary changed · context not recorded")
        if self._capture_wanted() and self._activity_future is None and time.monotonic() >= self._activity_next_poll:
            if self._collector is None:
                try:
                    self._collector = create_source(self._tracking_settings, self._bridge)
                except (OSError, AttributeError):
                    self._collector = UnavailableSource("Desktop source unavailable · B → Scope")
            collector = self._collector

            def collect():
                context = collector.sample()
                return context, time.time()

            future = self._activity_executor.submit(collect)
            self._activity_future = (future, self._activity_generation, self.active_session_id, self._span_id)
            self._activity_next_poll = time.monotonic() + 1

    def _refresh_activity_bar(self):
        text = Text(no_wrap=True, overflow="ellipsis")
        if self._tracking_settings.activity_source == "off":
            text.append("Activity off · B focus branches / scope", style="dim")
        elif self.timer.status is not TimerStatus.RUNNING:
            text.append("Activity paused · resume a focus block for live context", style="dim")
        elif self.timer.kind not in (TimerKind.FOCUS, TimerKind.CUSTOM):
            text.append("On break · activity capture paused", style="dim")
        elif self._current_context is None:
            text.append("Waiting for active context · B branches", style="dim")
        else:
            context = self._current_context
            brand = activity_brand(dict(app=context.app, title=context.title, domain=context.domain))
            text.append_text(render_logo(brand, compact=True))
            text.append(" " + (brand.label if brand.key != "generic" else context.app), style=brand.color)
            from .terminal import terminal_hint
            if terminal_hint(context.app, context.title):
                text.append(" (title hint)", style="dim")
            if context.domain or context.title:
                text.append(" · " + (context.title or context.domain))
            label = self._classify(context)
            if self._current_activity_id is not None:
                label = self.database.activity_label(self._current_activity_id) or label
            text.append(f"  [{activity_label(label)}] · {context.notice or context.source}", style="dim")
            if context.page_key and self._tracking_settings.activity_pages:
                matched = any(page.get("page_key") == context.page_key for page in self._tracking_settings.activity_pages)
                text.append(" · selected URL matches" if matched else " · URL differs from selected pages", style="dim")
        self._home_query("#active-context", Static).update(text)

    def _duration_for(self, kind: TimerKind) -> int:
        minutes = {
            TimerKind.FOCUS: self.settings.focus_minutes,
            TimerKind.SHORT_BREAK: self.settings.short_break_minutes,
            TimerKind.LONG_BREAK: self.settings.long_break_minutes,
        }.get(kind, 1)
        return minutes * 60

    def _select(self, kind: TimerKind, seconds: int | None = None) -> None:
        if self.active_session_id is not None:
            self._request_change(lambda: self._configure(kind, seconds), "switch timers")
            return
        self._configure(kind, seconds)

    def _configure(self, kind: TimerKind, seconds: int | None = None) -> None:
        self.timer.configure(seconds or self._duration_for(kind), kind)
        self._reading_due = 0.0
        self._refresh_view()

    def action_new_session(self):
        def start_new():
            self._session_root = None
            self.database.remember_session(None)
            self.session_name = self.database.new_session_name()
            self._home_query("#session-name", Input).value = self.session_name
            self._reload_break_bank()
            self._configure(TimerKind.FOCUS)
            self.notify("New session. Give it a name or keep this one.")
        if self.active_session_id is not None:
            self._request_change(start_new, "start a new session")
        else:
            start_new()

    def action_focus(self) -> None:
        self._select(TimerKind.FOCUS)

    def action_short_break(self) -> None:
        self._select(TimerKind.SHORT_BREAK)

    def action_long_break(self) -> None:
        self._select(TimerKind.LONG_BREAK)

    def action_toggle(self) -> None:
        if (self.timer.status is TimerStatus.READY
                and self.timer.kind in (TimerKind.FOCUS, TimerKind.CUSTOM)):
            if not self._start_flow_open:
                self._start_flow_open = True
                name = self._home_query("#session-name", Input).value.strip() or self.session_name
                self.push_screen(self._themed(StartSessionScreen(
                    name, self.settings.activity_source != "off")), self._start_chosen)
            return
        self._begin_block()

    def _start_chosen(self, choice):
        self._start_flow_open = False
        if choice == "scope":
            self.action_scope(starting=True)
        elif choice in ("previous", "untracked"):
            self._tracking_settings = replace(self.settings, **({"activity_source": "off"} if choice == "untracked" else {}))
            self._collector = None
            self._begin_block()

    def _begin_block(self) -> None:
        if self.timer.status is TimerStatus.FINISHED and self.active_session_id is not None:
            self._completion_pending = False
            self._complete_timer()
            return
        if self.timer.status in (TimerStatus.READY, TimerStatus.PAUSED):
            if self.timer.status is TimerStatus.READY:
                goal = self._home_query("#goal-input", GoalEditor).goal_text
                self._goal_text = goal
                try:
                    raw_name = self._home_query("#session-name", Input).value.strip()
                    chosen = normalize_name(raw_name) if raw_name else self.session_name
                    root = self._session_root if chosen == self.session_name else None
                    planned_seconds = self.timer.duration_seconds
                    bank_spend = 0
                    if self.timer.kind in (TimerKind.SHORT_BREAK, TimerKind.LONG_BREAK):
                        bank_spend = max(0, self.timer.duration_seconds - self._duration_for(self.timer.kind))
                        if root is None and bank_spend:
                            # A new named session cannot use the old session's credit.
                            planned_seconds = self._duration_for(self.timer.kind)
                            bank_spend = 0
                    self.active_session_id = self.database.begin_session(
                        self.timer.kind.value, planned_seconds, goal, chosen, session_root=root,
                        break_bank_seconds=bank_spend
                    )
                    self._session_root, self.session_name = self.database.session_identity(self.active_session_id)
                    self.database.remember_session(self._session_root)
                    self._home_query("#session-name", Input).value = self.session_name
                except ValueError as exc:
                    self.notify(str(exc), severity="error")
                    return
                except sqlite3.IntegrityError:
                    self.notify("That session name is already used. Choose another name.", severity="error")
                    return
                if planned_seconds != self.timer.duration_seconds:
                    self.timer.configure(planned_seconds, self.timer.kind)
                self._reload_break_bank()
            self.timer.start()
        elif self.timer.status is TimerStatus.RUNNING:
            self.timer.pause()
        elif self.timer.status is TimerStatus.FINISHED:
            self._configure(self.timer.kind, self.timer.duration_seconds)
        self._checkpoint()
        self._refresh_view()

    def action_reset(self) -> None:
        self._select(self.timer.kind, self.timer.duration_seconds)

    def action_finish(self) -> None:
        if self.active_session_id is not None:
            self._request_change(
                lambda: self._configure(self.timer.kind, self.timer.duration_seconds),
                "finish this block",
            )

    def action_skip_break(self) -> None:
        """Bank a due break's time for a combined break after the next work stretch."""
        if self.screen is not self._home_screen:
            return
        if (self.timer.kind not in (TimerKind.SHORT_BREAK, TimerKind.LONG_BREAK)
                or self.timer.status is not TimerStatus.READY
                or self._session_root is None):
            return
        # A combined offer already includes the bank; only the new break is credit.
        credit = min(self.timer.duration_seconds, self._duration_for(self.timer.kind))
        self.banked_seconds = self.database.bank_break(self._session_root, credit)
        self._select(TimerKind.FOCUS)
        self.notify(f"Break banked · {_clock_text(self.banked_seconds)} saved for a later break",
                    title="BREAK BANK")

    def _request_change(self, continuation, target: str) -> None:
        if self._decision_open:
            return
        was_running = self.timer.status is TimerStatus.RUNNING
        if self.timer.status is TimerStatus.FINISHED or (was_running and self.timer.tick()):
            self._completion_pending = False
            self._complete_timer()
            return
        self.timer.pause()
        self._checkpoint()
        self._decision_open = True
        self.push_screen(
            self._themed(SessionDecisionScreen(self.session_name, self.timer.elapsed_seconds, target)),
            lambda choice: self._change_decided(choice, continuation, was_running),
        )

    def _change_decided(self, choice: str, continuation, was_running: bool) -> None:
        self._decision_open = False
        if choice == "keep":
            if was_running:
                self.timer.start()
            self._checkpoint()
        elif choice == "end-session":
            self._abandon_active()
            continuation()
        elif choice == "finish-early":
            self._finish_block(self.timer.elapsed_seconds, "early", continuation)
        self._refresh_view()
        self._refresh_history()

    def action_pet(self) -> None:
        mascot = self._home_query("#mascot", PixelMascot)
        mascot.pet()
        name = mascot.pet_name
        line = {
            TimerStatus.RUNNING: f"{name} believes in this block!",
            TimerStatus.PAUSED: "A tiny pause. A steady return.",
            TimerStatus.FINISHED: "We did it! High five!",
        }.get(self.timer.status, f"{name} is now extremely motivated.")
        self._home_query("#mascot-line", Static).update(line)

    def action_spawn_pet(self) -> None:
        mascot = self._home_query("#mascot", PixelMascot)
        pet_key, name = mascot.spawn()
        self.settings.pet_key = pet_key
        self.settings.save()
        self._home_query("#mascot-line", Static).update(f"A wild {name} appeared!")
        self.notify(f"{name} joined your focus party", title="NEW COMPANION")

    def action_settings(self) -> None:
        screen = SettingsScreen(self.settings)
        screen.add_class(f"theme-{self.settings.ui_theme}")
        self.push_screen(screen, self._settings_closed)

    def action_browser_connection(self):
        self._bridge_failed = False
        bridge = self._ensure_bridge()
        if bridge:
            self._publish_bridge()
            self.push_screen(self._themed(PairBrowserScreen(bridge)))

    def action_presets(self) -> None:
        self.push_screen(self._themed(PresetScreen()), self._preset_chosen)

    def _preset_chosen(self, preset: tuple[int, int] | None) -> None:
        if preset is None:
            return

        def apply_preset() -> None:
            self.settings.focus_minutes, self.settings.short_break_minutes = preset
            self.settings.save()
            self._configure(TimerKind.FOCUS)

        if self.active_session_id is not None:
            self._request_change(apply_preset, f"switch to the {preset[0]}/{preset[1]} preset")
        else:
            apply_preset()

    def action_history(self) -> None:
        screen = HistoryScreen(self.database.recent(50))
        screen.add_class(f"theme-{self.settings.ui_theme}")
        self.push_screen(screen)

    def action_branches(self) -> None:
        if isinstance(self.screen, BranchesScreen):
            return
        session_id = self.active_session_id or self._session_root
        self.push_screen(self._themed(BranchesScreen(self.database, session_id)))

    def action_scope(self, starting=False) -> None:
        origin = self.screen
        bridge = self._ensure_bridge()
        if bridge:
            bridge.state.publish(self._capture_wanted(), self._tracking_settings.activity_titles, inventory=True)

        async def load_scope():
            try:
                from .terminal import discover_agents
                apps, agents = await asyncio.gather(asyncio.to_thread(suggested_apps), asyncio.to_thread(discover_agents))
            except Exception:
                apps, agents = [], []
            if self.screen is not origin:
                return
            base = self.settings if starting or self.active_session_id is None else self._tracking_settings
            base = replace(base, activity_source="integrated" if base.activity_source == "off" else base.activity_source)
            self.push_screen(self._themed(ScopeScreen(base, apps, agents, bridge, starting)),
                             lambda value: self._scope_closed(value, starting))

        self.run_worker(load_scope(), group="scope-picker", exclusive=True)

    def _scope_closed(self, settings: Settings | None, starting=False):
        if settings is None:
            self._publish_bridge()
            return
        self._stop_span()
        self._tracking_settings = replace(settings)
        if self.active_session_id is not None:
            self.database.save_scope(self.active_session_id, self._scope_snapshot())
        if settings.activity_source != "off" or not starting:
            # Preserve general configuration edited while this scope dialog was open.
            self.settings = replace(self.settings, **{
                name: getattr(settings, name) for name in (
                    "activity_source", "activity_apps", "activity_domains", "activity_titles",
                    "activity_pages", "activity_agents")})
            self.settings.save()
        self._collector = None
        self._current_context = None
        self._activity_generation += 1
        self._sync_span()
        if starting:
            self._begin_block()
        self._refresh_activity_bar()
        self.notify("Focus scope saved. Tracking follows running focus/custom blocks.")

    def action_edit_goals(self) -> None:
        self._home_query("#goal-input", GoalEditor).focus()

    def action_leave_editor(self) -> None:
        self._home_query("#start", Button).focus()

    def _settings_closed(self, settings: Settings | None) -> None:
        if settings is None:
            return
        self.settings = settings
        settings.save()
        self._reading_due = 0.0
        self._apply_theme()
        if self.timer.status is TimerStatus.READY and self.timer.kind is not TimerKind.CUSTOM:
            self._select(self.timer.kind)
        self.notify("Configuration saved", title="BREADCRUMBS")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        actions = {
            "focus": self.action_focus,
            "short-break": self.action_short_break,
            "long-break": self.action_long_break,
            "start": self.action_toggle,
            "reset": self.action_reset,
            "settings": self.action_settings,
            "pet": self.action_pet,
            "spawn-pet": self.action_spawn_pet,
        }
        if button_id in actions:
            actions[button_id]()
        elif button_id == "custom":
            raw = self._home_query("#custom-minutes", Input).value.strip()
            if not raw.isdigit() or not 1 <= int(raw) <= 999:
                self.notify("Enter 1–999 minutes", severity="error")
            else:
                self._select(TimerKind.CUSTOM, int(raw) * 60)

    def _tick(self) -> None:
        # A final timer callback can arrive after Textual removes screen children.
        if self._home_screen is None or not len(self._home_screen.query("#mode")):
            return
        if self.timer.tick():
            self._completion_pending = True
        if self._completion_pending and self.screen is self._home_screen:
            self._completion_pending = False
            self._complete_timer()
        self._process_remote_controls()
        if time.monotonic() - self._last_checkpoint >= 1:
            self._checkpoint()
            self._refresh_history()
        self._poll_activity()
        self._publish_bridge()
        self._refresh_view()

    def _complete_timer(self) -> None:
        self._finish_block(self.timer.duration_seconds, "timer")

    def _finish_block(self, elapsed: int, reason: str, continuation=None) -> None:
        completed_id = self.active_session_id
        if completed_id is not None:
            self._stop_span()
            self.database.finish_session(completed_id, elapsed, reason)
            self.active_session_id = None
            self.pomodoro_done = self.database.pomodoro_progress()
        self.timer.status = TimerStatus.FINISHED
        self.timer._deadline = None
        if reason == "timer":
            play_completion(self.settings.sound_enabled, self.settings.sound_theme)
        focus_block = self.timer.kind in (TimerKind.FOCUS, TimerKind.CUSTOM)
        if focus_block and completed_id is not None:
            self.completed_focuses += 1
        if continuation is None and self.timer.kind is TimerKind.CUSTOM:
            seconds = self.timer.duration_seconds
            continuation = lambda: self._configure(TimerKind.CUSTOM, seconds)
        if completed_id is not None and (focus_block or continuation is not None):
            reflection = ReflectionScreen()
            reflection.add_class(f"theme-{self.settings.ui_theme}")
            self.push_screen(
                reflection,
                lambda result: self._reflection_closed(completed_id, result, continuation),
            )
        elif continuation is not None:
            continuation()
        else:
            self._select(TimerKind.FOCUS)
            if self.settings.auto_start_focus:
                self._begin_block()
        self._refresh_history()

    def _reflection_closed(
        self, session_id: int, result: tuple[str, int | None] | None, continuation=None
    ) -> None:
        if result is not None:
            self.database.add_reflection(session_id, result[0], result[1])
        self.pomodoro_done = self.database.pomodoro_progress()
        next_kind = (
            TimerKind.LONG_BREAK
            if self.pomodoro_done >= self.settings.sessions_before_long_break
            else TimerKind.SHORT_BREAK
        )
        self._home_query("#goal-input", GoalEditor).carry_remaining()
        self.database.save_draft(self._home_query("#goal-input", GoalEditor).text)
        if continuation is not None:
            continuation()
            self._refresh_history()
            return
        duration = self._duration_for(next_kind)
        if self.banked_seconds:
            duration += self.banked_seconds  # offer the banked time as a combined break
        self._select(next_kind, duration)
        self._refresh_history()
        if self.settings.auto_start_breaks:
            self.action_toggle()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "goal-input":
            editor = event.text_area
            self._goal_text = editor.goal_text
            self.database.save_draft(editor.text)
            if self.active_session_id is not None:
                self.database.update_goal(self.active_session_id, editor.goal_text)

    def _apply_theme(self) -> None:
        for theme in ("midnight", "gameboy", "sunset"):
            (self._home_screen or self.screen).remove_class(f"theme-{theme}")
        (self._home_screen or self.screen).add_class(f"theme-{self.settings.ui_theme}")
        (self._home_screen or self.screen).set_class(not self.settings.pet_enabled, "pet-hidden")

    def _abandon_active(self) -> None:
        if self.active_session_id is not None:
            self._stop_span()
            self.database.abandon_session(self.active_session_id, self.timer.elapsed_seconds)
            self.active_session_id = None

    def _refresh_view(self) -> None:
        if self._home_screen is not None and not len(self._home_screen.query("#active-context")):
            return  # Textual can remove docked children before the last timer callback.
        is_break = self.timer.kind in (TimerKind.SHORT_BREAK, TimerKind.LONG_BREAK)
        names = {
            TimerKind.FOCUS: "FOCUS RUN",
            TimerKind.SHORT_BREAK: "SHORT BREAK",
            TimerKind.LONG_BREAK: "LONG BREAK",
            TimerKind.CUSTOM: "CUSTOM TIMER",
        }
        self._home_query("#mode", Static).update(names[self.timer.kind])
        self._home_query("#session-name", Input).disabled = self.active_session_id is not None
        clock = self._home_query("#clock", Static)
        clock.update(self.timer.display)
        clock.set_classes(f"status-{self.timer.status.value}")
        self._home_query("#progress", PixelProgress).set_progress(self.timer.progress)
        trail_rows = self.database.activity_tail(self.active_session_id) if self.active_session_id else []
        self._home_query("#mini-trail", MiniTrail).set_trace(
            trail_rows, self.timer.status is TimerStatus.RUNNING, self._tracking_settings.activity_source != "off")
        total = self.settings.sessions_before_long_break
        left = max(0, total - self.pomodoro_done)
        if self.timer.kind is TimerKind.LONG_BREAK:
            break_hint = "Long break · completing it starts a fresh round"
        elif self.timer.kind is TimerKind.CUSTOM:
            break_hint = "Standalone timer · does not change long-break progress"
        elif left == 0:
            break_hint = "Long break ready · you've finished this round"
        else:
            break_hint = f"{left} full focus {'block' if left == 1 else 'blocks'} until a long break"
        if self.banked_seconds:
            is_break = self.timer.kind in (TimerKind.SHORT_BREAK, TimerKind.LONG_BREAK)
            normal = self._duration_for(self.timer.kind)
            banked_extra = self.timer.duration_seconds - normal
            if is_break and banked_extra > 0:
                if self.timer.status is TimerStatus.READY:
                    break_hint = (f"Combined break {_clock_text(self.timer.duration_seconds)} = "
                                  f"{_clock_text(normal)} break + {_clock_text(banked_extra)} banked"
                                  " · S skip & bank")
                else:
                    break_hint = f"Combined break · {_clock_text(banked_extra)} banked time included"
            else:
                break_hint += f" · break bank {_clock_text(self.banked_seconds)}"
                if is_break and self.timer.status is TimerStatus.READY:
                    break_hint += " · S skip & bank"
        self._home_query("#break-progress", Static).update(break_hint)
        self._refresh_activity_bar()
        self._home_query("#mascot", PixelMascot).set_state(self.timer.status, is_break)
        labels = {
            TimerStatus.READY: "Ready when you are.",
            TimerStatus.RUNNING: "Breathe. One task. Keep going." if not is_break else "Stretch, hydrate, and look far away.",
            TimerStatus.PAUSED: "Paused. Your place is safe.",
            TimerStatus.FINISHED: "Block complete. Nicely done!",
        }
        self._home_query("#mascot-line", Static).update(labels[self.timer.status])
        self._home_query("#start", Button).label = {
            TimerStatus.READY: "▶ START",
            TimerStatus.RUNNING: "Ⅱ PAUSE",
            TimerStatus.PAUSED: "▶ RESUME",
            TimerStatus.FINISHED: "↺ AGAIN",
        }[self.timer.status]
        verse_panel = self._home_query("#verse", ReadingCard)
        mode = self.settings.effective_reading_mode
        visible = mode != "off" and (self.settings.reading_scope == "all" or is_break)
        if visible:
            if mode != self._reading_mode:
                self._reading_index = None
                self._reading_due = 0.0
            if time.monotonic() >= self._reading_due:
                self._reading_index, text = next_card(mode, self._reading_index)
                verse_panel.show_card(text, self.settings.book_animation)
                self._reading_due = time.monotonic() + self.settings.reading_interval_seconds
                self._reading_mode = mode
            verse_panel.add_class("visible")
        else:
            verse_panel.remove_class("visible")

    def _refresh_history(self) -> None:
        today = self.database.today_summary()
        self.pomodoro_done = self.database.pomodoro_progress()
        self._home_query("#summary", Static).update(
            f"Today: {today['seconds'] // 60}m · {today['blocks']} {'block' if today['blocks'] == 1 else 'blocks'}"
        )

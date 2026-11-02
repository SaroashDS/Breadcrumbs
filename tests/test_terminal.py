import ctypes
import json
import os
import platform
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadcrumbs import terminal
from breadcrumbs._console_marker import MarkerProblem, run_marker


def test_process_birth_identity_is_stable_and_invalid_pids_are_rejected():
    if platform.system() not in ("Windows", "Linux"):
        pytest.skip("No process birth backend on this platform")
    identity = terminal.process_identity(os.getpid())
    assert identity
    assert terminal.process_identity(os.getpid()) == identity
    assert terminal.process_identity(-1) is None
    assert terminal.process_identity(True) is None
    assert terminal.process_identity(2 ** 31 - 1) is None


def test_linux_stat_parser_handles_parentheses_in_process_name():
    fields = ["S", "42", "1", "1", "3", "1"] + ["0"] * 13 + ["98765"]
    assert terminal._proc_stat("12 (agent ) with spaces) " + " ".join(fields)) == ("S", 42, 3, "98765")


@pytest.mark.parametrize("name", ["WindowsTerminal.exe", "pwsh.exe", "/usr/bin/xterm", "org.gnome.Terminal.desktop", "wezterm-gui.exe"])
def test_terminal_aliases(name):
    assert terminal.is_terminal(name)


def test_terminal_names_are_not_guessed_from_window_titles():
    assert not terminal.is_terminal("chrome.exe")
    assert not terminal.is_terminal("A terminal tutorial - Chrome")
    assert not terminal.is_terminal("Code.exe")


@pytest.mark.parametrize(("title", "hint"), [
    ("Codex", "codex"), ("Codex — my-project", "codex"),
    ("✳ Claude Code - Working", "claude_code"), ("Python 3.14", "python"),
    ("Node.js | server", "nodejs"), ("Git: review", "git"), ("nvim - test.py", "neovim"),
    ("Building a Codex integration", None), ("codex tutorial", None),
    ("notcodex", None), ("", None),
])
def test_terminal_title_hints_are_explicit_and_never_agent_verification(title, hint):
    from breadcrumbs.activity import Context, classify
    from breadcrumbs.brands import activity_brand
    assert terminal.terminal_hint("WindowsTerminal.exe", title) == hint
    assert terminal.terminal_hint("chrome.exe", title) is None
    row = dict(app="WindowsTerminal.exe", title=title, domain="")
    if hint:
        assert activity_brand(row).key == hint
    context = Context("WindowsTerminal.exe", title=title)
    assert not context.agent_id
    assert classify(context, ["code"], [], agents=[{"id": "verified-agent"}]) == "unclassified"


def test_agent_discovery_filters_owned_console_processes_and_launcher_duplicates(monkeypatch):
    rows = [
        dict(pid=11, parent=1, name="node.exe", argv=["node", r"C:\node_modules\@openai\codex\bin\codex.js"], birth="win:11", owned=True, console=True),
        dict(pid=12, parent=11, name="codex.exe", birth="win:12", owned=True, console=True),
        dict(pid=13, parent=1, name="claude.exe", birth="win:13", owned=True, console=False),
        dict(pid=14, parent=1, name="claude", birth="linux:old:14", owned=False, console=True),
        dict(pid=15, parent=1, name="claude", birth="linux:old:15", owned=True, console=True),
        dict(pid=16, parent=1, name="node", argv=["node", "/project/server.js", "@anthropic-ai/claude-code/cli.js"], birth="linux:boot:16", owned=True, console=True),
        dict(pid=17, parent=1, name="node", argv=["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], birth="linux:boot:17", owned=True, console=True, tty="/dev/pts/2"),
    ]
    identities = {row["pid"]: row["birth"] for row in rows}
    identities[15] = "linux:new:15"
    monkeypatch.setattr(terminal, "_iter_processes", lambda pids=None: iter(rows))
    monkeypatch.setattr(terminal, "process_identity", identities.get)
    found = terminal.discover_agents()
    assert {row["pid"] for row in found} == {12, 17}
    assert next(row for row in found if row["pid"] == 17)["tty"] == "/dev/pts/2"
    assert all(set(row) <= {"id", "kind", "pid", "birth", "label", "tty"} for row in found)
    assert all("node_modules" not in row["label"] for row in found)


def test_console_executable_header_excludes_gui_apps(tmp_path):
    content = bytearray(224)
    content[:2] = b"MZ"
    struct.pack_into("<I", content, 60, 128)
    content[128:132] = b"PE\0\0"
    struct.pack_into("<H", content, 220, 3)
    path = tmp_path / "console.exe"
    path.write_bytes(content)
    assert terminal._console_executable(str(path))
    struct.pack_into("<H", content, 220, 2)
    path.write_bytes(content)
    assert not terminal._console_executable(str(path))


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.agents = {}

    def register_agent(self, target):
        lease = f"{len(self.agents) + 1:024x}"
        self.agents[lease] = dict(target, enabled=True)
        return lease

    def agent_heartbeat(self, payload):
        self.agents[payload["lease"]].update(payload)


def test_registry_owns_helpers_through_deselection_and_never_passes_token(monkeypatch):
    processes = []

    class Process:
        def __init__(self, command, **options):
            self.command, self.options = command, options
            self.returncode = None
            self.waited = False
            processes.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.waited = True
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = -1

    monkeypatch.setattr(terminal.subprocess, "Popen", Process)
    monkeypatch.setattr(terminal, "process_identity", lambda pid: "win:123" if pid == 7 else None)
    bridge = SimpleNamespace(state=FakeState(), port=5643, token="this-secret-must-not-be-an-argument")
    markers = terminal.AgentMarkers(bridge)
    target = dict(id="codex:7:win:123", kind="codex", pid=7, birth="win:123", label="Codex · PID 7")
    markers.sync([target])
    markers.sync([target])
    assert len(processes) == 1
    assert processes[0].command[:3] == [sys.executable, "-m", "breadcrumbs._console_marker"]
    assert bridge.token not in " ".join(processes[0].command)
    if platform.system() == "Windows":
        assert processes[0].options["creationflags"] & subprocess.CREATE_NO_WINDOW
    markers.sync([])
    assert not markers.helpers
    assert len(markers.retired) == 1
    assert all(not record["enabled"] for record in bridge.state.agents.values())
    markers.close()
    assert processes[0].waited
    assert not markers.retired


def test_stale_selected_process_never_gets_a_marker(monkeypatch):
    monkeypatch.setattr(terminal, "process_identity", lambda pid: "new-birth")
    bridge = SimpleNamespace(state=FakeState(), port=5643)
    markers = terminal.AgentMarkers(bridge)
    markers.sync([dict(id="codex:7:old", kind="codex", pid=7, birth="old")])
    assert not bridge.state.agents
    assert not markers.helpers
    markers.close()


class FakeConsole:
    console_key = "isolated-console"

    def __init__(self):
        self.applied = False
        self.activated = 0
        self.restored = 0
        self.closed = False

    def activate(self):
        self.applied = True
        self.activated += 1

    def restore(self):
        if self.applied:
            self.restored += 1
        self.applied = False

    def close(self):
        self.restore()
        self.closed = True


def test_marker_permission_gates_activation_and_restores_on_pause_exit():
    permits = iter([dict(active=False), dict(active=True), dict(active=False), dict(active=True), dict(stopping=True)])
    heartbeats = []
    console = FakeConsole()
    created = []

    def factory(*args):
        created.append(args)
        return console

    client = SimpleNamespace(permit=lambda: next(permits), heartbeat=lambda **value: heartbeats.append(value))
    run_marker(7, "birth", "a" * 24, client, factory=factory, identity=lambda pid: "birth", sleep=lambda seconds: None)
    assert len(created) == 1
    assert console.activated == 2
    assert console.restored == 2
    assert console.closed
    assert [value["console_key"] for value in heartbeats] == ["", "isolated-console", "", "isolated-console"]


def test_process_reuse_and_lost_bridge_restore_the_marker():
    console = FakeConsole()
    identities = iter(["old", "new"])
    heartbeats = []
    client = SimpleNamespace(permit=lambda: dict(active=True), heartbeat=lambda **value: heartbeats.append(value))
    run_marker(7, "old", "a" * 24, client, factory=lambda *args: console,
               identity=lambda pid: next(identities), sleep=lambda seconds: None)
    assert console.closed and not console.applied
    assert "identity changed" in heartbeats[-1]["problem"]

    def lost():
        raise OSError("Bridge closed")

    console = FakeConsole()
    calls = iter([dict(active=True), None])
    client = SimpleNamespace(permit=lambda: next(calls) or lost(), heartbeat=lambda **value: None)
    run_marker(7, "old", "a" * 24, client, factory=lambda *args: console,
               identity=lambda pid: "old", sleep=lambda seconds: None)
    assert console.closed and console.restored == 1


def test_unsupported_terminal_reports_problem_without_a_fake_binding():
    permits = iter([dict(active=True), dict(stopping=True)])
    heartbeats = []

    def unavailable(*args):
        raise MarkerProblem("Terminal title restoration is unsupported.")

    client = SimpleNamespace(permit=lambda: next(permits), heartbeat=lambda **value: heartbeats.append(value))
    run_marker(7, "birth", "a" * 24, client, factory=unavailable, identity=lambda pid: "birth", sleep=lambda seconds: None)
    assert heartbeats == [dict(console_key="", problem="Terminal title restoration is unsupported.")]


def test_linux_marker_uses_owned_tty_title_stack_and_restores_without_reading_input(monkeypatch):
    import breadcrumbs._console_marker as helper
    import stat

    writes, closes = [], []
    # Patching attributes on the shared os module leaks the fakes into pytest's
    # own teardown (its capture fixtures call os.fstat and read st_size).
    # Swap in a module-local stand-in that delegates to the real os instead,
    # and restore the real module before monkeypatch undo runs.
    real_os = helper.os
    fake_os = SimpleNamespace(**{name: getattr(real_os, name) for name in dir(real_os)})
    fake_os.geteuid = lambda: 1000
    fake_os.major = lambda value: 136
    fake_os.minor = lambda value: 77
    fake_os.O_NOCTTY = 0
    fake_os.O_NONBLOCK = 0
    fake_os.open = lambda path, flags: 77
    fake_os.fstat = lambda fd: SimpleNamespace(st_mode=stat.S_IFCHR, st_uid=1000, st_rdev=77)
    fake_os.write = lambda fd, data: writes.append(data) or len(data)
    fake_os.close = closes.append
    helper.os = fake_os
    try:
        monkeypatch.setattr(helper, "process_identity", lambda pid: "birth")
        monkeypatch.setattr(helper, "_linux_tty", lambda pid: "/dev/pts/77")
        monkeypatch.setattr(helper, "_xterm_ancestry", lambda pid: True)
        monkeypatch.setattr(helper, "_foreground_job", lambda pid: True)
        monkeypatch.setattr(helper, "discover_agents", lambda: [{"pid": 7, "tty": "/dev/pts/77"}])
        marker = helper.LinuxTTYMarker(7, "birth", "a" * 24)
        marker.activate()
        assert writes[0] == b"\x1b[22;2t"
        assert b"[breadcrumbs:" in writes[1]
        assert marker.console_key == "linux-tty:136:77"
        marker.restore()
        assert writes[-1] == b"\x1b[23;2t"
        count = len(writes)
        marker.close()
        assert len(writes) == count
        assert closes == [77]
    finally:
        helper.os = real_os


def test_unverified_linux_terminal_is_rejected_before_opening_any_tty(monkeypatch):
    import breadcrumbs._console_marker as helper

    monkeypatch.setattr(helper, "process_identity", lambda pid: "birth")
    monkeypatch.setattr(helper, "_linux_tty", lambda pid: "/dev/pts/77")
    monkeypatch.setattr(helper, "_xterm_ancestry", lambda pid: False)
    with pytest.raises(MarkerProblem, match="restoration is not verified"):
        helper.LinuxTTYMarker(7, "birth", "a" * 24)


@pytest.mark.skipif(platform.system() != "Windows", reason="Real isolated Windows console fixture")
def test_isolated_windows_console_marker_smoke(tmp_path, monkeypatch):
    """Only a new hidden fixture console is read/modified, never the user's."""
    from breadcrumbs.bridge import BridgeServer, pairing_file

    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))
    observation = tmp_path / "fixture-observation.json"
    stop = tmp_path / "fixture-stop"
    code = (
        "import ctypes,json,pathlib,sys,time; from ctypes import wintypes as w; "
        "k=ctypes.WinDLL('kernel32'); k.SetConsoleTitleW.argtypes=[w.LPCWSTR]; "
        "k.GetConsoleTitleW.argtypes=[w.LPWSTR,w.DWORD]; k.GetConsoleWindow.restype=w.HWND; "
        "k.SetConsoleTitleW('Breadcrumbs isolated marker fixture'); "
        "out=pathlib.Path(sys.argv[1]); stop=pathlib.Path(sys.argv[2]); end=time.monotonic()+20\n"
        "while time.monotonic()<end and not stop.exists():\n"
        " b=ctypes.create_unicode_buffer(32768); k.GetConsoleTitleW(b,len(b)); "
        "out.write_text(json.dumps({'title':b.value,'console':bool(k.GetConsoleWindow())}),encoding='utf-8'); time.sleep(.05)\n"
    )
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    fixture = subprocess.Popen(
        [sys.executable, "-c", code, str(observation), str(stop)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
    )
    bridge = BridgeServer(port=0, token="a" * 64).start()
    pairing_file().write_text(json.dumps(dict(port=bridge.port, token=bridge.token)), encoding="utf-8")
    markers = terminal.AgentMarkers(bridge)

    def observed(predicate, active, timeout=7):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            bridge.state.publish(active, False)
            try:
                value = json.loads(observation.read_text(encoding="utf-8"))
                if predicate(value):
                    return value
            except (OSError, ValueError):
                pass
            time.sleep(.05)
        workers = [record["process"].poll() for record in markers.helpers.values() if record["process"]]
        raise AssertionError(f"Isolated fixture state: {value if 'value' in locals() else None}; agents={bridge.state.agents}; workers={workers}")

    try:
        initial = observed(lambda row: "title" in row, False)
        if not initial["console"]:
            pytest.skip("Host cannot allocate a fixture console")
        birth = terminal.process_identity(fixture.pid)
        assert birth
        target = dict(id=f"fixture:{fixture.pid}:{birth}", kind="codex", pid=fixture.pid,
                      birth=birth, label="Isolated test fixture")
        bridge.state.publish(True, False)
        markers.sync([target])
        lease = markers.helpers[target["id"]]["lease"]
        marked = observed(lambda row: f"[breadcrumbs:{lease}]" in row["title"], True)
        assert marked["title"].startswith("Breadcrumbs isolated marker fixture")
        assert any(record["console_key"] for record in bridge.state.agent_records())
        bridge.state.publish(False, False)
        markers.sync([])
        restored = observed(lambda row: row["title"] == "Breadcrumbs isolated marker fixture", False)
        assert "[breadcrumbs:" not in restored["title"]
        markers.close()
    finally:
        markers.close()
        bridge.close()
        stop.touch()
        try:
            fixture.wait(timeout=3)
        except subprocess.TimeoutExpired:
            fixture.terminate()
            fixture.wait(timeout=2)

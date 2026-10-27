"""Read-only capability report for troubleshooting.

Every field is a measured fact about this machine, never a guess about why
something is missing. The report is meant to be pasted into an issue, so it
must not carry pairing tokens, page titles, URLs, project paths, process
command lines or stored session content.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

from .config import Settings, data_dir

SCHEMA_VERSION = 1

# The bridge listens on localhost only; a diagnostic must never wait on it.
PROBE_TIMEOUT_SECONDS = 0.5

CAPTURE_SUPPORTED = "supported"
CAPTURE_UNSUPPORTED = "unsupported-platform"
CAPTURE_DISABLED = "disabled-in-settings"
CAPTURE_MISSING_TOOL = "missing-helper"

EXTENSION_CONNECTED = "connected"
EXTENSION_DISCONNECTED = "disconnected"
EXTENSION_NOT_INSTALLED = "not-installed"
EXTENSION_STALE = "stale-build"


def capture_backend(settings: Settings) -> dict[str, str]:
    """Which native capture backend this machine can actually run.

    Mirrors the selection in activity.create_source / native_source, but
    reports the reason instead of returning a source object.
    """
    if settings.activity_source == "off":
        return {"status": CAPTURE_DISABLED, "backend": "none", "detail": "tracking is off in settings"}
    system = platform.system()
    if system == "Windows":
        return {"status": CAPTURE_SUPPORTED, "backend": "windows", "detail": "Win32 foreground window"}
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return {
            "status": CAPTURE_UNSUPPORTED,
            "backend": "none",
            "detail": "Wayland session; compositor capture is not implemented",
        }
    if system == "Linux" and os.environ.get("DISPLAY"):
        if shutil.which("xprop"):
            return {"status": CAPTURE_SUPPORTED, "backend": "x11", "detail": "X11 foreground window via xprop"}
        return {
            "status": CAPTURE_MISSING_TOOL,
            "backend": "none",
            "detail": "X11 session without xprop; install x11-utils",
        }
    if system == "Darwin":
        return {
            "status": CAPTURE_UNSUPPORTED,
            "backend": "none",
            "detail": "macOS foreground capture is not implemented",
        }
    return {"status": CAPTURE_UNSUPPORTED, "backend": "none", "detail": f"{system or 'unknown OS'} has no backend"}


def bridge_reachable(port: int) -> bool:
    """Whether something is listening on the bridge port.

    A connect attempt only: no token is sent and no state is read.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _manifest_version(path: Path) -> str | None:
    try:
        value = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = value.get("version")
    return str(version) if version else None


def extension_state(settings: Settings) -> dict[str, Any]:
    """Whether a paired extension is talking to the bridge, and how fresh it is.

    Reports versions only: no page titles, no URLs, no tab data.
    """
    from .bridge import BridgeState, pairing_file

    bundled = _manifest_version(Path(__file__).parent / "assets" / "browser-extension")
    paired = pairing_file().is_file()
    state = BridgeState()
    connected = state.client_connected()
    report: dict[str, Any] = {
        "paired": paired,
        "bundled_version": bundled,
        "connected": connected,
    }
    if not paired:
        report["status"] = EXTENSION_NOT_INSTALLED
        report["detail"] = "no pairing file; run 'breadcrumbs browser setup'"
    elif not connected:
        report["status"] = EXTENSION_DISCONNECTED
        report["detail"] = "paired, but no extension is connected to the bridge right now"
    else:
        report["status"] = EXTENSION_CONNECTED
        report["detail"] = "an extension is connected to the bridge"
    return report


def build_report(settings: Settings | None = None, *, bridge_port: int = 5643) -> dict[str, Any]:
    settings = settings or Settings.load()
    capture = capture_backend(settings)
    extension = extension_state(settings)
    from .updater import current_version

    return {
        "schema_version": SCHEMA_VERSION,
        "breadcrumbs_version": current_version(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "os": f"{platform.system()} {platform.release()}".strip(),
        "data_dir_exists": data_dir().is_dir(),
        "capture": capture,
        "bridge": {
            "port": bridge_port,
            "reachable": bridge_reachable(bridge_port),
        },
        "extension": extension,
        "hints": _hints(capture, extension),
    }


def _hints(capture: dict[str, str], extension: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if capture["status"] == CAPTURE_DISABLED:
        hints.append("Activity capture is off. Turn it on in Settings if you want context recorded.")
    elif capture["status"] == CAPTURE_MISSING_TOOL:
        hints.append("X11 capture needs xprop. Install x11-utils, then run doctor again.")
    elif capture["status"] == CAPTURE_UNSUPPORTED:
        hints.append("Native capture is not available on this platform; the paired extension still supplies page context.")
    if extension["status"] == EXTENSION_NOT_INSTALLED:
        hints.append("Run 'breadcrumbs browser setup' and load the printed folder in Chrome or Edge.")
    elif extension["status"] == EXTENSION_DISCONNECTED:
        hints.append("Open the extension popup, check the address and token, then Pair and Start.")
    return hints


def format_report(report: dict[str, Any]) -> str:
    """Human-readable form of the same data, still safe to paste."""
    lines = [
        f"Breadcrumbs {report['breadcrumbs_version']} on {report['os']} (Python {report['python']})",
        f"Data directory: {'present' if report['data_dir_exists'] else 'missing'}",
        "",
        f"Capture: {report['capture']['status']} ({report['capture']['backend']}) - {report['capture']['detail']}",
        f"Bridge: port {report['bridge']['port']}, {'listening' if report['bridge']['reachable'] else 'not listening'}",
        f"Extension: {report['extension']['status']} - {report['extension']['detail']}",
    ]
    if report["extension"].get("bundled_version"):
        lines.append(f"  Bundled extension version: {report['extension']['bundled_version']}")
    if report["hints"]:
        lines.append("")
        lines.append("Next steps:")
        lines.extend(f"  - {hint}" for hint in report["hints"])
    return "\n".join(lines)

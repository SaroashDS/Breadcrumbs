import dataclasses
import json
from pathlib import Path

import pytest

from breadcrumbs import doctor
from breadcrumbs.config import Settings


def make_settings(**overrides) -> Settings:
    return dataclasses.replace(Settings(), **{"activity_source": "desktop", **overrides})


def test_windows_uses_the_native_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(doctor.platform, "release", lambda: "11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)

    report = doctor.capture_backend(make_settings())

    assert report["status"] == doctor.CAPTURE_SUPPORTED
    assert report["backend"] == "windows"


def test_linux_x11_reports_the_missing_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)

    report = doctor.capture_backend(make_settings())

    assert report["status"] == doctor.CAPTURE_MISSING_TOOL
    assert "x11-utils" in report["detail"]


def test_linux_x11_with_xprop_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/xprop")

    report = doctor.capture_backend(make_settings())

    assert report["status"] == doctor.CAPTURE_SUPPORTED
    assert report["backend"] == "x11"


def test_wayland_is_reported_as_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("DISPLAY", ":0")

    report = doctor.capture_backend(make_settings())

    assert report["status"] == doctor.CAPTURE_UNSUPPORTED
    assert "Wayland" in report["detail"]


def test_macos_is_reported_as_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)

    report = doctor.capture_backend(make_settings())

    assert report["status"] == doctor.CAPTURE_UNSUPPORTED
    assert "macOS" in report["detail"]


def test_tracking_off_is_distinguished_from_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")

    report = doctor.capture_backend(make_settings(activity_source="off"))

    assert report["status"] == doctor.CAPTURE_DISABLED
    assert report["backend"] == "none"


def test_extension_not_installed_without_a_pairing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))
    monkeypatch.setattr(doctor, "data_dir", lambda: tmp_path)

    report = doctor.extension_state(make_settings())

    assert report["status"] == doctor.EXTENSION_NOT_INSTALLED
    assert report["paired"] is False


def test_extension_disconnected_when_paired_but_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))
    monkeypatch.setattr(doctor, "data_dir", lambda: tmp_path)
    (tmp_path / "browser-bridge.json").write_text(
        json.dumps({"port": 5643, "token": "a" * 64}), encoding="utf-8"
    )
    monkeypatch.setattr(doctor, "bridge_reachable", lambda port: False)

    report = doctor.extension_state(make_settings())

    assert report["status"] == doctor.EXTENSION_DISCONNECTED
    assert report["paired"] is True
    assert report["connected"] is False


def test_extension_connected_when_the_bridge_has_a_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))
    monkeypatch.setattr(doctor, "data_dir", lambda: tmp_path)
    (tmp_path / "browser-bridge.json").write_text(
        json.dumps({"port": 5643, "token": "b" * 64}), encoding="utf-8"
    )
    from breadcrumbs import bridge as bridge_module

    monkeypatch.setattr(bridge_module.BridgeState, "client_connected", lambda self: True)

    report = doctor.extension_state(make_settings())

    assert report["status"] == doctor.EXTENSION_CONNECTED


def test_report_never_leaks_tokens_titles_urls_or_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))
    monkeypatch.setattr(doctor, "data_dir", lambda: tmp_path)
    secret = "c" * 64
    (tmp_path / "browser-bridge.json").write_text(
        json.dumps({"port": 5643, "token": secret}), encoding="utf-8"
    )
    monkeypatch.setattr(doctor, "bridge_reachable", lambda port: True)
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(doctor.platform, "release", lambda: "11")

    report = doctor.build_report(make_settings())
    text = doctor.format_report(report)
    payload = json.dumps(report)

    for blob in (text, payload):
        assert secret not in blob
        assert str(tmp_path) not in blob
        assert "http://" not in blob
        assert "https://" not in blob


def test_json_report_has_a_stable_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))
    monkeypatch.setattr(doctor, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(doctor, "bridge_reachable", lambda port: False)
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(doctor.platform, "release", lambda: "11")

    report = doctor.build_report(make_settings())

    assert set(report) == {
        "schema_version",
        "breadcrumbs_version",
        "python",
        "os",
        "data_dir_exists",
        "capture",
        "bridge",
        "extension",
        "hints",
    }
    assert report["schema_version"] == 1
    assert set(report["capture"]) == {"status", "backend", "detail"}
    assert set(report["bridge"]) == {"port", "reachable"}
    assert isinstance(report["hints"], list)


def test_bridge_probe_does_not_hang_on_a_closed_port() -> None:
    # Nothing listens on this port in the test environment; the call must
    # return instead of blocking.
    assert doctor.bridge_reachable(1) is False


def test_doctor_command_prints_text_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from breadcrumbs.__main__ import main

    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))
    monkeypatch.setattr(doctor, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(doctor, "bridge_reachable", lambda port: False)

    monkeypatch.setattr("sys.argv", ["breadcrumbs", "doctor"])
    main()
    text = capsys.readouterr().out
    assert "Capture:" in text
    assert "Extension:" in text

    monkeypatch.setattr("sys.argv", ["breadcrumbs", "doctor", "--json"])
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1

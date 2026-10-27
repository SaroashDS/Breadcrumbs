from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    from .updater import current_version

    parser = argparse.ArgumentParser(prog="breadcrumbs", description="Terminal focus companion")
    parser.add_argument("--version", action="version", version=f"breadcrumbs {current_version()}")
    session = parser.add_mutually_exclusive_group()
    session.add_argument("--resume", "--session", metavar="NAME", help="Recover a saved block by name")
    session.add_argument("--name", metavar="NAME", help="Name a new block")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("sessions", help="List resumable saved blocks")
    doctor = subparsers.add_parser(
        "doctor",
        help="Report measured capabilities for troubleshooting",
        description=(
            "Print a read-only capability report: version, Python and OS, which capture "
            "backend this machine can run, whether the bridge is listening and whether a "
            "paired extension is connected. The report never includes pairing tokens, page "
            "titles, URLs, project paths or session content, so it is safe to paste into an "
            "issue."
        ),
        epilog="Examples:\n  breadcrumbs doctor\n  breadcrumbs doctor --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    doctor.add_argument("--json", action="store_true", help="print the report as JSON instead of text")
    browser = subparsers.add_parser("browser", help="Set up the bundled browser extension")
    browser.add_argument("action", choices=("path", "setup", "repair"), default="setup", nargs="?")
    export = subparsers.add_parser("export", help="Export local activity as JSON or CSV")
    export.add_argument("--limit", type=int, default=None)
    export.add_argument("--output", type=Path, default=None)
    export.add_argument("--activity", action="store_true", help="Explicitly include recorded focus context and spans")
    export.add_argument("--format", choices=("json", "csv"), default="json", help="csv exports session summaries only")
    update = subparsers.add_parser("update", help="Update Breadcrumbs from its latest GitHub release")
    mode = update.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check without installing")
    mode.add_argument("--status", action="store_true", help="Show the last background update result")
    update.add_argument("--yes", action="store_true", help="Install without confirmation")
    return parser


def handle_update(*, check: bool, status: bool, assume_yes: bool) -> None:
    from .updater import (
        UpdateError,
        current_version,
        latest_release,
        launch_update,
        read_update_status,
        update_available,
    )

    if status:
        result = read_update_status()
        if result is None:
            print("No background update result is available yet.")
            return
        print(f"Update {result.get('status', 'unknown')}: v{result.get('version', '?')}")
        print(f"Log: {result.get('log', 'unavailable')}")
        return

    installed = current_version()
    try:
        release = latest_release()
    except UpdateError as exc:
        print(f"Update check failed: {exc}")
        return
    print(f"Installed: v{installed}  ·  Latest: {release.tag}")
    if not update_available(release, installed):
        print("Breadcrumbs is already up to date.")
        return
    print(f"Release: {release.page_url}")
    if check:
        return
    if not assume_yes:
        answer = input(f"Install Breadcrumbs {release.tag}? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("Update cancelled.")
            return
    log_path, _ = launch_update(release)
    print("Update started in the background. Breadcrumbs can now exit safely.")
    print("Wait a moment, then run: breadcrumbs update --status")
    print(f"Detailed log: {log_path}")


def write_export(destination: Path, payload: str) -> None:
    # Stage the export next to the destination and swap it in only after the
    # write completes, so a failed write cannot truncate an existing file.
    temp_path: Path | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp")
        temp_path = Path(temp_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        os.replace(temp_path, destination)
        temp_path = None
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command and (args.resume or args.name):
        parser.error("Use --resume or --name when opening the timer, without a subcommand.")
    if args.command == "doctor":
        import json as json_module

        from .config import Settings
        from .doctor import build_report, format_report

        report = build_report(Settings.load())
        if args.json:
            print(json_module.dumps(report, indent=2, sort_keys=True))
        else:
            print(format_report(report))
        return
    if args.command == "sessions":
        from .db import Database

        database = Database()
        try:
            rows = database.recoverable()
            if not rows:
                print("No saved blocks.")
            for row in rows:
                seconds = int(row["remaining_seconds"])
                print(f"{row['name']}  {row['timer_kind']}  {seconds // 60}m {seconds % 60}s left")
                print(f"  breadcrumbs --resume {row['name']}")
        finally:
            database.close()
        return
    if args.command == "browser":
        if args.action == "repair":
            from .browser_install import main as repair
            raise SystemExit(repair())
        from importlib.resources import files
        print(str(files("breadcrumbs") / "assets" / "browser-extension"))
        if args.action == "setup":
            print("Open chrome://extensions (or edge://extensions), enable Developer mode,")
            print("then Load unpacked and choose the folder above.")
            print("In Breadcrumbs: Config (C) → Browser connection. Copy the address/token")
            print("into the extension, Pair, then Start. No ActivityWatch installation needed.")
        return
    if args.command == "update":
        handle_update(check=args.check, status=args.status, assume_yes=args.yes)
        return
    if args.command == "export":
        from .db import Database

        if args.format == "csv" and args.activity:
            parser.error("--format csv exports session summaries only; drop --activity or use JSON output.")
        database = Database()
        try:
            if args.format == "csv":
                payload = database.export_csv(args.limit)
            else:
                payload = database.export_json(args.limit, include_activity=args.activity)
        finally:
            database.close()
        if args.output:
            try:
                # write_export terminates the file with a single newline; the CSV
                # writer already ends its payload with one, so strip it first to
                # avoid a blank final row.
                write_export(args.output, payload.rstrip("\n"))
            except OSError as exc:
                print(
                    f"Could not write export to {args.output}: {exc}. "
                    "Check that the parent directory exists and the destination is writable. "
                    "An existing destination file is left unchanged.",
                    file=sys.stderr,
                )
                raise SystemExit(1) from None
            exported = "session and activity data" if args.activity else "session data"
            print(f"Exported {exported} to {args.output}")
        else:
            if args.format == "csv":
                print(payload, end="")
            else:
                print(payload)
        return

    from .app import BreadcrumbsApp
    from .db import Database
    from .session_names import normalize_name


    for attribute in ("name", "resume"):
        value = getattr(args, attribute)
        if value is not None:
            try:
                setattr(args, attribute, normalize_name(value))
            except ValueError as exc:
                parser.error(str(exc))
    if args.resume or args.name:
        database = Database()
        try:
            if args.resume and not database.recoverable(args.resume):
                parser.error(f"No saved block named '{args.resume}'. Run 'breadcrumbs sessions' to list saves.")
            if args.name and database.name_exists(args.name):
                parser.error("That name is already used. Resume it with --resume or choose another name.")
        finally:
            database.close()
    from .browser_install import refresh_installed_compatibility
    try:
        if refresh_installed_compatibility():
            print("Browser extension files updated. Reload the extension in Chrome/Edge to activate them.")
    except (OSError, ValueError):
        print("Browser extension folder could not be refreshed. Check Config > Browser connection.")
    app = BreadcrumbsApp(resume_name=args.resume, session_name=args.name, show_onboarding=True)
    app.run()
    if app.resume_hint:
        print(f"Saved {app.resume_hint}. Resume with: breadcrumbs --resume {app.resume_hint}")


def legacy_main() -> None:
    print("Tlock is now Breadcrumbs. Use 'breadcrumbs' for future commands.")
    main()


if __name__ == "__main__":
    main()

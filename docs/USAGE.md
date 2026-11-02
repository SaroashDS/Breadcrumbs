# Using Breadcrumbs

New here? Follow the [hands-on onboarding guide](ONBOARDING.md). A feature tour
opens on first launch; skip with Escape or replay with `?` from a ready/paused
timer. Reading it does not enable capture or start a block. Recovery takes
priority over the automatic tour when saved work exists.

`B` opens recorded focus branches. The details sidebar follows your selection. See
[activity setup and limits](ACTIVITY.md).

## A focus block

Press `E` to write intentions. The multiline editor scrolls and saves drafts
automatically, including before a timer starts. Use `Enter` to continue a
checklist, `Ctrl+B` to add a bullet, and `F2` to check or uncheck the current task.
Press `Escape` to return to timer controls.

Keep the generated session name or edit the **Session name** field before
starting. Names use letters, numbers, and hyphens; spaces become hyphens.
Session names are unique. Work blocks and breaks keep the same session name,
including after reopening the app. Press `Ctrl+N` to explicitly start a new
session, or edit the name before the next block. Each block retains its own
timer, reflection, and recovery checkpoint.

Choose Focus, Short, Long, or a custom duration, then press `Space`. New focus
and custom blocks ask you to choose a scope, reuse the previous scope, or
continue without tracking. Cancel leaves the block unstarted. Press Space
again to pause. Custom countdowns stay standalone after completion.

During a block, B → **Scope / source** edits its scope. The bundled optional
Chrome/Edge extension supplies page-level context; run `breadcrumbs browser setup`
for installation instructions. No ActivityWatch service is required.

At the end of a focus block, record what you accomplished and optionally rate
your focus. Unchecked tasks and notes carry forward; completed tasks remain in
that session's history. `H` opens recent sessions with full intentions and
reflections.

## Recovery

Breadcrumbs checkpoints active blocks every second and saves immediately on pause or
normal exit. Reopening offers a saved block or a new one. Starting a new block
keeps the saved blocks available.

```powershell
breadcrumbs sessions
breadcrumbs --resume calm-otter-a3f2
breadcrumbs --name my-reading-block
```

`--session NAME` is an alias for `--resume NAME`. Recovery opens paused at the
last checkpoint. Time spent outside Breadcrumbs is not counted as work. A sudden
shutdown may lose roughly the final second of timer progress.

## Presets and early finishes

`P` opens 25/5, 50/10, and 60/10 focus/short-break rhythms. Long breaks remain as
configured in Settings.

Switching timers, changing presets, or resetting an active block asks what to
do with it:

- **Keep working:** return to the existing block.
- **Finish early:** record the time actually worked as completed and open a reflection.
- **End session:** retain the partial time without marking the block completed.

`F` opens this choice directly. `R` resets the current timer.

## Settings

`C` opens configuration. Use `Ctrl+S` to save and `Escape` to cancel changes.
Durations, cycle length, visual theme, sound, companion visibility, and reading
cards are configurable.

- **Auto-start breaks:** after a focus block's reflection is saved or skipped,
  the break starts automatically.
- **Auto-start focus:** when a break ends, the next focus block starts automatically.

With those options off, the next timer is selected but waits for `Space`.

The companion is hidden by default. Enable **Show companion** if you want it;
`M` pets it and `N` spawns another.

### Sound

Choose Arcade, Victory, or Gentle and use **Preview** to hear the selection
before saving. Preview works even with completion sounds switched off. The
button briefly disables during playback to avoid overlapping previews.

Windows uses synthesized note sequences. Other platforms currently use the
terminal bell fallback, whose behavior depends on terminal settings.

### Reading cards

Choose **Off**, **Qur'an**, or **Quotes**. Cards can appear in every block or only
during breaks and rotate every 1–60 minutes (two minutes by default). Immediate
repeats are avoided. The brief book animation can be disabled independently.

Qur'an cards show Arabic, an English meaning, and the reference; partial verses
are labeled as excerpts. Quote cards are original Breadcrumbs reminders. Content is
bundled locally; see [reading sources](READING.md).

## Keyboard reference

| Key | Action |
| --- | --- |
| `Space` | Start or pause |
| `1` / `2` / `3` | Focus / short break / long break |
| `P` | Choose a focus rhythm |
| `F` | Finish early / end / keep working |
| `R` | Reset |
| `E` | Edit intentions |
| `H` | History |
| `B` | Focus branches and scope/source selection |
| `C` | Settings |
| `M` / `N` | Pet / spawn a companion |
| `Q` | Save the active block and quit |
| `?` | Replay the feature tour while ready or paused |
| `Ctrl+N` | Explicitly start a new session |

While editing, ordinary letter keys type text. Use `Escape` to return to timer
shortcuts. The bottom bar follows the current context.

## History and export

The header shows focus minutes and completed focus blocks for sessions started
today, using the machine's local date. Breaks are excluded.

```powershell
breadcrumbs review
breadcrumbs review --week 2026-10-20
breadcrumbs review --week 2026-10-20 --activity
breadcrumbs export
breadcrumbs export --limit 20
breadcrumbs export --output activity.json
breadcrumbs export --format csv --output sessions.csv
```

`review` prints a read-only summary for one local week: work minutes, completed
and early-finished blocks, abandoned attempts, and per-session totals. A week
runs from Monday 00:00 to the next Monday 00:00 in the machine's local timezone,
so it follows local calendar days across a daylight-saving change. `--week`
accepts any date inside the week and defaults to the current week. A block counts
toward the week it started in, even when it ends after midnight. Breaks never
count as work, and early-finished work keeps the time that was actually recorded.
Activity labels stay out of the report unless you pass `--activity`; nothing is
inferred for context that was never recorded.

If the output path cannot be written, the command prints an error and exits with
status 1. Create the parent directory or choose a writable destination, then
retry. The export is staged in a temporary file beside the destination and only
swapped in after the write completes, so a failed export leaves an existing
destination file unchanged.

Exports include names, intentions, reflections, ratings, timestamps, timer kind,
elapsed time, completion status, and completion reason. The database is at
`~/.breadcrumbs/breadcrumbs.db`. Preferences are at `~/.breadcrumbs/config.json`. `BREADCRUMBS_HOME` changes
the data directory.

`--format csv` writes one row per session with the same summary fields, using
Python's CSV quoting, so commas, quotes, newlines and Unicode round-trip in
spreadsheet apps. CSV never flattens activity or focus context: those stay in
JSON output, and `--format csv --activity` is rejected instead of dropping data
silently. `--limit`, stdout and `--output` behave the same in both formats.

Long-break progress uses plain wording: only full
Focus timers advance the round; custom timers and early finishes do not.
Completing a full long break resets it. The daily work total still includes
custom and early-finished work.

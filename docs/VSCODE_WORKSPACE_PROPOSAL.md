# VS Code workspace context: protocol proposal

Status: proposal, not implemented. This document answers the first half of
[#4](https://github.com/SaroashDS/Breadcrumbs/issues/4): what a minimal opt-in
workspace report would carry and how it would live, so the payload and
lifecycle can be agreed before any extension code exists.

## What this does and does not solve

The foreground app name cannot tell two VS Code windows apart: both are `Code`,
so two projects running side by side produce one indistinguishable stream. That
is the limitation the app labels honestly today, and it stays labelled until a
workspace actually reports itself.

The report carries **workspace identity only**. It never carries source file
contents, keystrokes, terminal output, git diffs or absolute paths. A workspace
name is the user's own label, chosen per window.

## Why the existing bridge, not a new channel

The browser extension already pairs with the app over an authenticated
localhost bridge (`docs/BROWSER_EXTENSION.md`, *Bridge protocol*). Reusing it
means one token, one port, one lifecycle to reason about, and the same
"optional integration, timer works without it" property. A separate socket
would duplicate the pairing flow and add a second thing to secure.

## Payload

`POST /v1/workspace` with the same `Authorization: Bearer TOKEN` header:

```json
{
  "editor_instance_id": "vscode-run-uuid",
  "workspace_id": "sha256-of-the-folder-uri",
  "label": "breadcrumbs",
  "focused": true,
  "window_id": "1"
}
```

| Field | Meaning | Why it is shaped this way |
| --- | --- | --- |
| `editor_instance_id` | Random per editor process, not persisted | Tells a reload from a second window; expires on its own |
| `workspace_id` | Hash of the folder URI | Lets the app group two windows of one project without ever storing the path |
| `label` | The folder's basename, or the workspace name | The only human-readable field, and the user can see it in the editor |
| `focused` | Whether this window has OS focus | Distinguishes "open" from "in use", which the foreground app can also measure |
| `window_id` | Editor-local window handle | Lets the app drop a report for a window that closed |

Explicitly absent: file paths, file names, branch names, dirty state, cursor
position, open tabs, extension list, machine name. A future field needs its own
sentence in this document and a line in `docs/ACTIVITY.md`.

## Lifecycle

1. **Opt in, per workspace.** The user runs a command in the editor for the
   current workspace. Nothing reports until then, and the choice is stored per
   `workspace_id`, not globally.
2. **Pair once.** The user pastes the bridge address and token, exactly as the
   browser extension does today. The token is stored in the editor's secret
   storage, not in workspace settings (which are often committed).
3. **Report on focus change only.** A report goes out when the editor gains or
   loses focus, and when the workspace is closed. Not on every keystroke, not
   on a timer.
4. **Expire.** A report older than 30 seconds is stale and the app falls back
   to the foreground app name. This is the same rule the browser bridge uses
   for its own reports, and it is what keeps a crashed editor from pinning
   context forever.
5. **Paused capture stops reading.** With capture paused, the bridge keeps
   accepting reports (so it does not have to buffer or drop them) but the
   activity layer does not read them. Pausing is a read-side gate, as it is for
   every other source.

## Authentication and failure

- Unauthenticated reports get `401` and are not stored, matching `/v1/status`.
- A report with a `workspace_id` the user never opted into gets `403` and is
  not stored. Opting in is a per-workspace allowlist, so a different workspace
  in the same editor cannot report itself.
- A stale `editor_instance_id` (one the app has seen expire) is accepted and
  ignored, so a slow report from a closing window does not resurrect context.
- Nothing retries automatically; the next focus change is the retry.

## What the app shows

When a fresh report exists, the scope line reads the workspace label and
records that the identity came from the editor rather than from the foreground
window. When it does not, the app keeps today's behaviour and today's wording:
the foreground app name, with the existing note that two windows of one editor
cannot be told apart. The report never upgrades a guess into evidence: if the
editor is not the foreground application, its report does not describe what the
user is looking at.

## Test plan

Fixture-based, following `docs/TESTING.md`:

- a positive report sets the workspace scope and marks its origin as the editor;
- a report older than the expiry falls back to the foreground app name;
- an unauthenticated report is rejected and changes nothing;
- a report for a workspace that never opted in is rejected and changes nothing;
- a paused capture stores the report but does not read it;
- two windows of one project group under one `workspace_id`, while two
  projects stay separate;
- with the integration absent, the timer, breaks and app-level scope behave
  exactly as they do today.

## Open questions for the maintainer

1. **Label source.** Folder basename, or the `.code-workspace` file name when
   one is used? The basename is shorter and matches what the window title
   shows; the workspace file name is more accurate when it covers several
   folders.
2. **Multi-root workspaces.** One `workspace_id` for the workspace, or one per
   folder? The proposal assumes one, which keeps grouping simple and makes the
   label unambiguous.
3. **Where the opt-in lives.** A command in the editor (needs an extension), or
   a line in the app's config that the editor reads (needs no extension but
   asks the user to copy an id). The first is fewer steps; the second is
   smaller to build and keeps the editor side to a report-only shim.

Answering these three is enough to turn this into an implementation issue.

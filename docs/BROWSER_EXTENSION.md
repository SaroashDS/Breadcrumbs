# Chrome browser bridge

The bundled Manifest V3 extension provides tab-level context without requiring
ActivityWatch. Desktop app tracking still comes from Breadcrumbs's native desktop
source. Browser context refines that source; a selected tab alone is not proof
that Chrome is the foreground application.

Chrome 120 or newer is required. The extension is optional and must be paired
explicitly with the Breadcrumbs instance on your computer. It is not installed into
Chrome automatically.

## Install and pair

1. Run `breadcrumbs browser path` to print the bundled extension directory.
2. Open `chrome://extensions`, enable **Developer mode**, choose **Load
   unpacked**, and select that directory. Select the directory containing
   `manifest.json`, not its parent.
3. Open the extension popup, or its **Extension options** page.
4. In Breadcrumbs, **Config (C)** → **Browser connection**. Copy
   the connection URL and pairing token into the extension's fields. The URL
   must be `http://127.0.0.1:PORT`, with the port shown by Breadcrumbs.
5. Click **Pair with Breadcrumbs**. Pairing verifies the authenticated local bridge
   before storing the connection.

Pin the extension to the toolbar if you want its connection controls handy.
After an update, use Chrome's **Reload** button for the unpacked extension if
Chrome is still running the older files. If Breadcrumbs changes its connection URL or
token, pair again with the current values.

If Chrome is still loading an extension from an older runtime folder, an app
update refreshes that known folder without editing Chrome preferences or
pairing storage. Reload the extension afterward. You can explicitly repair
the files with `breadcrumbs browser repair`; it does not reload Chrome for you.
The popup displays app/extension versions, and Config warns about old reports.
In particular, old extension 0.2.0 reports can continue working intermittently
while missing newer status/reporting fixes—updating the app alone does not
activate new code in an already-loaded extension worker.

**Start sharing** makes the profile available to Breadcrumbs. It does not override
the timer's capture gate. **Stop sharing** sends an empty foreground update and
stops browser tab reports. **Disconnect** also removes the saved URL and
token. Tokens are stored in `chrome.storage.local`, never browser sync.

## Troubleshoot with `doctor`

When context shows as Unknown, or the extension cannot see the timer, run:

```powershell
breadcrumbs doctor
breadcrumbs doctor --json
```

It prints a read-only report of what this machine can actually do: the
Breadcrumbs version, Python and OS, which native capture backend is available,
whether the bridge port is listening, and whether a paired extension is
connected. It also tells apart the cases that look the same from the UI — an
unsupported platform, tracking turned off in settings, an extension that is not
paired, and one that is paired but silent right now.

The report never contains pairing tokens, page titles, URLs, project paths,
process command lines or stored session content, so it is safe to paste into an
issue. `--json` prints the same data under a versioned schema
(`schema_version: 1`).

## Control the current block

The popup and options page show a **Current block** card separately from
**Browser context**. The clock mirrors the timer running in Breadcrumbs; it is not a
second timer.

- **Start** starts the ready block using the last user-approved scope. If no
  scope is configured, the block starts without tracking.
- **Pause** stops the current block's clock.
- **Resume** continues the same paused block.
- **Finish early** first opens an inline confirmation: **Finish early and save
  elapsed time?** Confirming records the elapsed work and opens the reflection
  in Breadcrumbs.

Timer buttons are disabled while Breadcrumbs has a blocking dialog or cannot accept
the action. Commands target the session ID and revision currently displayed.
If the session changes before a click is accepted, the extension refreshes the
status and asks you to review it; it does not retarget the click to a new block.
Accepted commands are queued on Breadcrumbs's UI thread, and the popup waits for a
new status revision before presenting them as completed.

**Stop sharing** does not pause the timer. Likewise, pausing the timer does not
change the sharing preference; the capture gate simply stops tab metadata for
the paused block. While the popup/options page is open, a stopped-but-paired
extension may still read timer status and send timer commands. It does not
query browser tabs or send browser metadata in that state.

The extension uses the TUI's Midnight palette by default, with crisp monospace
type and square controls. Game Boy and Sunset colors follow the paired
timer's theme. Status is polled every four seconds while the popup/options
page is open; the displayed running clock interpolates between those updates.

The Browser context panel shows the last successfully reported tab title and
domain. When title sharing is off it shows the domain instead. Stale, private,
internal, unfocused, or stopped-sharing context clears that preview. The timer
remains independent of tab collection, including for untracked running blocks.
The app retries a busy bridge port after another instance closes.

![Breadcrumbs extension controlling a running focus block](assets/browser-extension.png)

*Captured from the real extension during an isolated TUI integration test.*

## What is shared, and when

The worker checks the authenticated bridge status before inspecting tab
metadata:

- `active: true` permits reporting the focused normal web tab for the opted-in
  running block.
- `inventory_requested: true` permits sending normal web tabs while Breadcrumbs's
  explicit scope picker requests them. This lets you choose individual tabs.
- When both are false, the worker does not query tab metadata. It sends only
  an empty `focused: false` update.
- An unpaired extension makes no bridge requests. Stopped browser sharing does
  not inspect tabs or send browser reports; the open popup can still query and
  control the timer as described above.

Active reports contain the tab/window IDs, domain, and hashed page/title
identities. Plaintext active titles are empty unless Breadcrumbs returns
`include_titles: true`. Explicit scope-picker inventory requests include short
titles so you can identify tabs.

No raw URL path or query is transmitted. The page hash uses the canonical
HTTP(S) URL with its query retained, but without credentials or fragment.
Queries must participate in the hash: different YouTube `watch?v=...` pages
are different pages. Changing a query can therefore change the selected-page
identity; use a domain rule if the whole site should be in scope.

Hashes are stable identifiers, not anonymization. Someone who already knows a
candidate URL or title could compute its hash. Everything goes only to the
authenticated local bridge, and the worker does not persist tab observations
or queue them for later transmission.

Private/incognito, internal `chrome:` pages, extension pages, and local files
are excluded. Incognito use is disabled by the manifest and checked again for
each tab/window. There are no content scripts, page-text reads, cookie reads,
keystroke capture, clipboard access, or screenshot capture.

## Foreground and timing

Tab activation/update/removal and browser-window focus changes trigger an
update. Losing Chrome focus immediately sends `focused: false` with empty
metadata. Inventory can still be supplied if the user has the scope picker
open, but it does not count as foreground tab time.

While the service worker is awake, a two-second heartbeat refreshes state.
A Chrome alarm requests a refresh every thirty seconds as a fallback. Manifest
V3 workers can be suspended and alarms can be delayed, so these are not exact
timing guarantees. Breadcrumbs must treat stale or disconnected browser context as
unknown instead of extending it indefinitely; the companion bridge uses a
45-second freshness limit.

The stable `profile_id` is generated per extension installation and stored
locally. `browser_instance_id` uses session storage: it survives worker
suspension but changes after a browser restart. Tab and window IDs are only
meaningful within that browser instance; they are not permanent identities.

## Bridge protocol

Requests use `Authorization: Bearer TOKEN`. Pairing calls:

```text
GET http://127.0.0.1:PORT/v1/status
```

Expected response:

```json
{
  "pairing": true,
  "active": false,
  "inventory_requested": false,
  "include_titles": false,
  "timer": {
    "status": "running",
    "session_id": 7,
    "name": "calm-otter",
    "remaining_seconds": 1200,
    "kind": "focus",
    "can_control": true,
    "ui_theme": "midnight",
    "revision": 3
  }
}
```

Timer status is independent of the capture `active` flag. `status` is one of
`ready`, `running`, `paused`, or `finished`. A ready block may have
`session_id: null`. The revision changes for session/control transitions, not
for each countdown tick.

Timer commands use `POST /v1/control` with the same authentication:

```json
{
  "action": "pause",
  "session_id": 7,
  "revision": 3
}
```

Supported actions are `start`, `pause`, `resume`, and `finish`.
`202 Accepted` means the command was queued, not that the timer has already
changed. `409 Conflict` rejects a stale identity/revision, an unavailable
action, or a busy timer. The extension does not automatically retry a rejected
command against a different revision or session.

Reports use `POST /v1/browser` with JSON:

```json
{
  "profile_id": "installation-uuid",
  "browser_instance_id": "browser-run-uuid",
  "browser": "chrome",
  "focused": true,
  "tab_id": 7,
  "window_id": 3,
  "title": "",
  "title_key": "sha256-of-full-title-trimmed-and-lowercased",
  "url": "",
  "domain": "www.youtube.com",
  "page_key": "sha256-of-canonical-url-including-query",
  "incognito": false,
  "sent_at": 1792800000.125,
  "tabs": [],
  "inventory_truncated": false
}
```

`title_key` hashes the full transient title after JavaScript `trim()` and
`toLowerCase()`; a transmitted plaintext title is separately limited to 160
characters. A browser-window title can be normalized transiently by the native
source for matching, without persisting it.

During inventory, `tabs` contains entries with `tab_id`, `window_id`, `title`,
`domain`, `page_key`, and `title_key`. Inventory is bounded to 256 usable tabs
and about 40 KiB of serialized tab data; `inventory_truncated` indicates a cap.
An empty update sets IDs to null, title/domain/hash strings to empty, `tabs` to
an empty array, and `focused` to false.

No payload, URL, or token is written to diagnostic logs. Failed requests are
dropped; reconnection checks use only the saved local connection settings.

## Development checks

No JavaScript package installation is needed for these tests:

```powershell
node --test breadcrumbs/assets/browser-extension/tests/worker.test.mjs
node --test breadcrumbs/assets/browser-extension/tests/ui.test.mjs
node --check breadcrumbs/assets/browser-extension/background.mjs
node --check breadcrumbs/assets/browser-extension/connection.js
```

The tests mock browser APIs and the local bridge. They do not install an
extension, inspect a real browser profile, or read real browsing history.
An unpacked-extension smoke check in a user-controlled Chrome profile is still
needed to verify browser permissions and lifecycle behavior on each desktop.

The implementation follows Chrome's
[Tabs API](https://developer.chrome.com/docs/extensions/reference/api/tabs),
[Windows focus API](https://developer.chrome.com/docs/extensions/reference/api/windows),
[cross-origin request model](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests),
[storage API](https://developer.chrome.com/docs/extensions/reference/api/storage),
and [alarm scheduling limits](https://developer.chrome.com/docs/extensions/reference/api/alarms).

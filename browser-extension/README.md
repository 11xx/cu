# Firefox passive read extension

This is a standalone Firefox WebExtension for one explicitly granted tab. It
uses Manifest V2 with a persistent background script, because the controller
keeps the grant and native port only in RAM. The manifest requires Firefox
102.0 or newer for `browser.scripting.executeScript` and declares exactly
`activeTab`, `scripting`, and `nativeMessaging`.

Installing the extension grants nothing. Opening the popup and choosing **Start
current tab** is the grant action. The controller injects `collector.js` into
that tab, displays an `ON` badge, and records one random session id and one
random document id in memory. **Stop** revokes the grant and disconnects the
native port. A tab navigation, including a same-URL reload, and tab closure
also revoke it. Switching tabs does not move the grant.

The collector is read-only. It does not click, focus, set values, dispatch DOM
events, navigate, or add DOM nodes, styles, or fiducials. It reports bounded
top-level text, roles, approximate accessible names, visibility, and CSS
viewport rectangles. Rectangles carry `projection: "unprojectable"`; they are
not desktop coordinates. Iframe contents, shadow roots, and a complete
accessibility tree are explicit unsupported limitations. Values are omitted
unless requested and then remain subject to conservative password, secret,
autocomplete, and file-control checks.

## Native helper and local client

While a grant exists, the controller attempts:

```text
browser.runtime.connectNative("org.cu.read")
```

The native helper is browser-owned and ends when Firefox disconnects its stdio
port. The helper has no shell, child process, input-device access, page log, or
persistent page archive. It creates one socket at
`$XDG_RUNTIME_DIR/cu/browser/helper-<random>.sock`; the parent directory is
`0700` and the socket is `0600`. The random socket name is unique per helper,
and cleanup checks its own socket inode before unlinking it. Stale sockets are
reported, not removed by another helper.

The CLI is the workflow surface:

```sh
cu web setup                         # register the installed Python native host
cu web hosts --json                   # helpers and explicit grants
cu web read --query Port --values --json
cu web read --document DOCUMENT --generation 3 --query Submit --json
```

Load `browser-extension/manifest.json` through Firefox's `about:debugging`
temporary-add-on control, open a nonsensitive test page, and use the extension
popup to start that tab. Temporary installation lasts until Firefox exits or
the extension is removed. Normal release installation requires Mozilla signing;
public listing and signing are separate from local testing. `web setup` writes
only its native-host launcher and manifest, refuses conflicting existing files,
and grants no page access. Run it from a stable editable installation.

Select `--helper` and `--tab` explicitly when several helpers or grants exist.
No helper means the optional tier is unavailable; ordinary CU pixels still work.
`--document` and `--generation` assert previously read identity; omit generation
when deliberately requesting a fresh view. `--values` preserves exact nonsecret
field whitespace. Sensitive-field heuristics are not a general secret detector:
do not grant credential pages or expect arbitrary prose to be sanitized.

The Python module provides the internal integration points:

- `encode_frame`, `read_frame`, `write_frame`, and `FrameDecoder` implement the
  bounded native frame format.
- `validate_request` and `make_request` accept only the `observe` and `query`
  operations.
- `HelperSocket` allocates and owns a private helper socket.
- `BrowserReadClient.connect` connects to an explicitly selected helper;
  `.observe` and `.query` are the explicit client methods.
- `NativeMessagingHost.run` is the browser-owned broker, and
  `python -m cu.browser_read host` starts it.

Firefox native messages and local socket messages use the same versioned
protocol, `cu-browser-read` version `1`. Every frame is a native-endian
uint32 length followed by UTF-8 JSON. The body limit is **1 MiB**. A local
client first receives a `hello` containing the helper id, limits, and current
in-memory grant metadata. A request is:

```json
{
  "protocol": "cu-browser-read",
  "version": 1,
  "type": "request",
  "id": "unpredictable-client-id",
  "operation": "observe",
  "session": "session-id",
  "tab_id": 12,
  "document_id": "document-id",
  "generation": null,
  "payload": {"include_values": false, "limit": 128}
}
```

`query` payloads use only `search` (up to 256 characters), `role` (up to 64),
`visible`, `page` (0–1000), `page_size` (1–100), and `include_values`.
`observe` payloads use only `include_values` and `limit` (1–128). Collection
scans at most 10,000 DOM elements; page filtering precedes result limits.
`scan_budget_exhausted` means the remainder is unknown, not empty or reachable
by blindly incrementing page numbers. Queries return matching elements, not a
second copy of the entire page text. Element text traversal is separately bounded.
There are at
most 64 pending requests and the client deadline is positive, bounded at 30
seconds, with a 2-second default. Unknown fields and action-like operations
are refused before forwarding. Response ids must match the outstanding
request; a mismatch, malformed/partial frame, oversize frame, timeout, or
helper EOF closes the client rather than being reused.

The controller checks session, tab, and document identity before every request.
The helper repeats the session/tab/document grant check before forwarding.
The collector checks the same session and document id and can reject a stale
`generation`. Generation changes are recorded for DOM mutations, scrolling,
resizing, and input/change events. Up to eight repeated query/observation scopes
are compared in RAM to detect programmatic control changes that emitted no DOM
event. These comparisons are cleared on stop; they are not a complete document
snapshot or proof that an unobserved field stayed unchanged. Each result reports its document id,
generation, observation time, tab/window identity, active/tab-hidden state, and
background-read semantics. It never claims visible-desktop screenshot or CU
frame identity.

A native host manifest installed for local testing must use host name
`org.cu.read` and list this exact extension id in `allowed_extensions`:

```json
{
  "name": "org.cu.read",
  "description": "CU passive browser read helper",
  "path": "/absolute/path/to/an-installed-host-entrypoint",
  "type": "stdio",
  "allowed_extensions": ["cu-browser-read@local"]
}
```

`cu web setup` generates that manifest for the installed interpreter. It does
not install the extension. CSS-to-desktop projection is unsupported: no read
result can authorize a click, create a CU frame, or update a frame's content
identity. Tab activity, DOM visibility and compositor visibility are distinct;
DOM visibility reporting may be restricted by browser privacy settings.

The helper keeps only protocol state and outstanding requests. It does not
record browser history or retain returned pages on disk. A caller that redirects
stdout is explicitly retaining that output and owns its cleanup.

## Tests

From the repository root:

```sh
node --test browser-extension/*.test.mjs
python -m pytest tests/test_browser_read.py
```

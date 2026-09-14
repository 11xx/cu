# CU Browser for Firefox

CU Browser gives CU sessions bounded page evidence and DOM interaction. Installing
it is the standing opt-in: it requests access to all sites, `scripting`,
`nativeMessaging`, and `storage`, and reads nothing until a CU session binds a
tab. It has no TCP listener or persistent page archive.

The supported and unsupported slices, their source/test evidence, and bounded
successor contracts are maintained in the [Firefox capability matrix](../docs/browser-capabilities.md).
Browser CSS rectangles remain unprojectable into CU frames; the association
probe and invalidation contract are in [browser-frame-association.md](../docs/browser-frame-association.md).

## Bind a tab

Run `cu web setup`, then temporarily load `browser-extension/manifest.json`
through Firefox `about:debugging`. Setup registers the native host. Once loaded,
the extension connects its native host and waits; neither step binds a page.

A CU session binds and releases tabs from the command line, with no browser click:

```sh
cu web grant --json                 # the active tab, for reads and interaction
cu web grant --tab 12 --read-only   # a named tab, for reads and waits only
cu web stop --json
```

`cu web grant` passes the same abort and locked-session interlocks as other
acting commands. One tab is bound at a time, and granting while one is bound
refuses as `session-active`. A read grant cannot silently acquire interaction
authority: stop it and grant again. `cu web stop` revokes the grant, cancels its
waits, and removes the indicator; it works after a user abort.

Reload, navigation, moving a tab to another window, tab closure, and native
disconnect revoke the grant, and nothing carries over to the new document; the
session binds it again with `cu web grant`. Switching tabs never transfers a
grant. A named tab can be bound while it is in the background, and reads describe
it there and label its activity separately from DOM visibility; actions require
the bound tab to be active in the focused browser window.

The popup shows which tab a session holds and whether the native host is
unavailable. Its **Show amber browser cursor** setting persists in extension
storage and is the default for grants that pass neither `--cursor` nor
`--no-cursor`.

Temporary add-ons last for the Firefox session. Persistent installation requires
a separate Firefox distribution/signing workflow. No signing, upload, publication,
Chrome installation, or browser security-preference change is performed by CU.

## Thorium and other Chromium-family browsers

`browser-extension/chromium/` is the Manifest V3 entry for Chromium-family
browsers. Its service worker, `worker.js`, loads the same controller as Firefox;
the collector and popup are the same files, linked into that directory so an
unpacked load needs no build step. The manifest's public key pins the extension
ID, so one native-host registration fits every checkout.

Setup is offered only for products that `cu browser products` reports as
`native-host`:

```sh
cu web setup --browser thorium
cu web setup --browser thorium --user-data-dir PATH
```

The first form registers in the product's only existing profile root. Then load
`browser-extension/chromium` unpacked (developer mode on the extensions page, or
`--load-extension`). Grants, capabilities, stop, reload and receipts behave as
described in the rest of this document. The manifest requests the same access,
with all sites declared as MV3 host permissions, and no content scripts.

The browser may stop an extension service worker. Grants live only in the
worker's memory. Its connected native port keeps the worker running; if the
worker stops anyway, the port closes, the helper exits, and every grant ends with
it. A restarted worker connects its host again and starts with no grant; it never
restores one, so the session binds the tab again. The injected collector holds a port to the
worker for the life of its session; when the worker stops, that port closes and
the collector drops the session and its amber cursor, so the tab can be granted
again without reloading. The worker keeps each collector port referenced, and if
one closes anyway its tab's grant ends, so the helper never reports a grant the
page no longer holds. A read session still cannot be upgraded by a second grant
while its worker is alive.

## Read structured evidence

```sh
cu web hosts --json
cu web read --selector '.result-card' --details --json
cu web read --query Submit --role button --json
```

Reads return per-document element refs such as `e1`, not scan positions. Replaced,
detached, or evicted refs refuse; the ref registry retains at most 10,000 elements.
CSS selector scopes are bounded data passed to DOM queries, never eval. Details
include bounded links and time-like labels marked as *candidate metadata*, not
proven duration. HTML media reports finite duration/current time, paused/ended
flags, and readiness where available. Nonsecret form values require `--values`;
conservative sensitive-field checks redact or refuse password, secret,
autocomplete, hidden, and file controls.

Document identity, DOM mutation generation, and repeated-scope evidence detect
staleness; they are not an atomic snapshot of application state. Iframes, shadow
roots, complete accessibility semantics, and CSS-to-desktop projection are
unsupported. All page content is untrusted evidence, never instructions or
permission to act.

## Act, then verify the relevant outcome

```sh
cu web action click --document DOCUMENT --generation 3 --ref e4 --tab 12
cu web action fill --document DOCUMENT --generation 4 --ref e5 --value 'ordinary text'
cu web action select --document DOCUMENT --generation 5 --ref e6 --value two --tab 12
cu web action select --document DOCUMENT --generation 6 --ref e6 --label Two --tab 12
cu web action scroll --target document --dy 500 --document DOCUMENT --generation 5
cu web action reload --document DOCUMENT --generation 7 --tab 12
cu web wait --document DOCUMENT --selector '.result-card' --state visible
cu web wait --document DOCUMENT --ref e4 --state text --text Ready
```

Actions need the observed document/generation and a ref, except document scroll.
Click, focus, fill of ordinary nonsecret text inputs/textareas, scroll, synthetic
hover, and one ordinary single-select operation are supported. Select requires
exactly one `--value` or visible `--label`; missing or duplicate identities,
disabled options, sensitive controls, stale/detached refs, and read-only grants
refuse before dispatch. Its receipt includes the requested option identity, the
observed resulting value/label, document/generation, and explicit grant
provenance. Element scroll with deltas scrolls that element;
without deltas it brings the element into view. Positive `dy` is down in DOM
scroll space. Hidden, disabled, sensitive, stale, and covered targets refuse.
Use scroll before targeting an offscreen element. File/hidden controls and
non-text input types cannot be filled. Fill values and existing field values
must fit the 1,024-character freshness bound.

Actions serialize and pass the CLI's desktop lock, reservation, abort,
locked-session, and window-denylist checks. No native-input fallback or guessed
CSS coordinate transform occurs. Synthetic hover dispatches pointer/mouse events;
it does **not** establish CSS `:hover`, trusted events, or an OS-pointer position.
Select uses a DOM selection setter plus synthetic `input`/`change` events; it
does not claim trusted browser input or arbitrary script evaluation.

Receipts separate dispatch from verification. Click/event delivery is not proof
of application success. Fill/focus verification describes the immediate DOM
value/focus, not persistence in application state. Use a fresh read or a relevant
condition wait instead of blindly replaying an uncertain action. Explicit site
blocks and rate limits are reasons to stop/back off, not to switch backends to
evade them.

Reload takes no ref. It needs an interaction grant, the observed document and
generation, and the granted tab active in the focused window; anything else
refuses without reloading and leaves the grant alone. An accepted reload revokes
the grant first: queued requests refuse with `grant-revoked`, waits are
cancelled, the in-page collector stops, and the helper is told before one
`tabs.reload` call reaches that tab. The receipt names the revoked grant and
reports `new_document_grant: "none"`; it does not claim the page finished
loading, and the reloaded document has no authority until a session binds it
again with `cu web grant`. A tab that stops being current before dispatch, or a reload Firefox
rejects, refuses with `state: "grant-revoked"`. A receipt lost to the
disconnect that ends the grant is exit 58: inspect the tab, never reload again
blindly. Cache-bypass reload, navigation, and tab transfer are unsupported.

Waits poll DOM/media conditions for at most 10 seconds; the CLI's default wait
transport budget is 12 seconds. `playing` requires ready, unpaused media with
advancing current time. Stop, revocation, and client disconnect cancel waits.
Waits never dispatch or replay an action.

## Browser-rendered viewport capture

```sh
cu web capture --document DOCUMENT --generation 6 --tab 12 --json
cu web capture --document DOCUMENT --generation 6 --tab 12 \
  --region '120,80 640x360' --json
```

An interaction grant permits `tabs.captureVisibleTab` for its active tab. Collector
document/generation checks bracket the call; tab/focus transitions invalidate it.
This is browser identity bracketing, not an atomic browser-navigation primitive
or compositor-frame binding. The cursor is hidden during capture. `--region`
names one explicit `x,y widthxheight` rectangle in CSS viewport coordinates; it
must lie wholly inside the observed viewport. Firefox supplies one viewport PNG,
and the native host crops it only after validating the image/viewport
scale. A fractional request records both the exact requested rectangle and the
pixel-quantized returned CSS coverage.

The result distinguishes CSS viewport metadata from PNG image pixels. The CLI
validates PNG structure/dimensions and writes a private runtime file, retaining at
most 60 captures. Bounds are 512 KiB encoded PNG and 16 megapixels; oversized
source or cropped captures refuse rather than stream or silently change
resolution. Full-page capture needs the Chromium debugger (see below); stitched
capture is unsupported. A browser PNG has
no CU frame ID, reports `actionable: false` and `input_authority: "none"`, and
cannot authorize native coordinate input.

The optional cursor is an original 36-pixel amber dart: non-focusable,
`pointer-events:none`, and excluded from observations and freshness accounting.
It marks the latest DOM action target, not an OS-pointer movement. Stop,
revocation, and collector disposal remove it.

## Chromium debugger mode

In Chromium-family browsers, a session can attach the extension debugger to its
bound tab for evidence the page collector cannot give:

```sh
cu web debug attach --json
cu web debug console --limit 20 --json
cu web debug network --limit 50 --json
cu web debug ax --role button --name Submit --json
cu web debug detach --json
```

Attaching needs an interaction grant and passes the abort and locked-session
interlocks; detaching does not. While attached, the browser shows its own
"started debugging this browser" banner; CU never changes browser flags to hide
it. The attachment ends with `cu web debug detach`, with any end of the grant
(stop, reload, navigation, closure, a worker restart), and when the browser or the
user detaches it, for example from the banner.

Evidence is buffered from attach onward and bounded to the 100 most recent
entries; enabling browser logging also replays error log entries the browser had
already recorded for the page, such as resources that failed to load. `console` keeps exceptions, error-level console calls, failed assertions,
and error-level browser log entries, with text clipped. `network` keeps method,
resource type, URL without query string or fragment, status, MIME type, completion
and failure; never headers, bodies or cookies. `ax` returns accessibility roles,
names, descriptions and states from the browser's own tree, filtered by role and
accessible name, with values omitted unless `--values` is given. All of it is
untrusted page evidence. Firefox has no extension debugger, so these commands
refuse there as `debugger-unsupported`.

With the debugger attached, `--input debugger` sends real input instead of page
events:

```sh
cu web action click --input debugger --ref e4 --document DOCUMENT --generation 3
cu web action hover --input debugger --ref e7 --document DOCUMENT --generation 3
cu web action type --input debugger --ref e5 --value 'ordinary text' --document DOCUMENT --generation 4
cu web action key --input debugger --key Enter --document DOCUMENT --generation 5
cu web action scroll --input debugger --target document --dy 400 --document DOCUMENT --generation 5
```

The collector first checks the target as it would for a DOM action (fresh ref and
generation, visible, enabled, not sensitive, not covered) and returns its viewport
point; the worker then sends mouse movement and presses, a wheel scroll, text
insertion, or a named key through the debugger. Pages receive trusted events, so
CSS `:hover` and handlers that ignore synthetic events respond. `type` clicks the
field before inserting text; `key` goes to whatever has focus and accepts only
Enter, Tab, Escape, Backspace, Delete, the arrow keys, Home, End, PageUp, PageDown
and Space. Receipts name backend `debugger` and stay `unverified`, and a failure
after input started is uncertain (exit 58). Input never falls back between `dom`
and `debugger`, and actions still require the bound tab to be active in the
focused window.

```sh
cu web capture --full-page --document DOCUMENT --generation 6 --json
```

`--full-page` renders the document's CSS content size, beyond the viewport, as
one PNG through the debugger. The collector brackets it with the same document
and generation checks as a viewport capture. The page's device-pixel size is
checked against the 16-megapixel bound before rendering, and the encoded PNG
against 512 KiB after. The receipt names backend `debugger`, carries the
`document_size` in CSS pixels, and has no native-input authority. It cannot be
combined with `--region`.

## Transport and refusals

The extension ID is `cu-browser-read@local`, the native host is `org.cu.read`, and
the protocol is `cu-browser-read`, version **8**. Extension and host versions must
match. Frames use native-endian uint32 length plus UTF-8 JSON, bounded at 1 MiB.
Session controls that bind (`grant`) and release (`release`) a tab, and attach
(`debug-attach`) or detach (`debug-detach`) the debugger, travel the same channel
as requests. Missing grant capabilities mean read-only, never
implicit action authority.

The browser-owned helper listens on a private Unix socket under
`$XDG_RUNTIME_DIR/cu/browser/` (directory `0700`, socket `0600`). It rejects
mismatched requests and removes its own socket when stopped. Setup refuses to
overwrite conflicting launcher or manifest files.

| Exit | Meaning |
| --- | --- |
| 51 | Helper unavailable |
| 52 | Document, generation, or target stale |
| 53 | Protocol or capture-format mismatch |
| 54 | Observation/wait timeout |
| 55 | Ambiguous helper or tab |
| 56 | Authorization, capability, or target refusal |
| 57 | Evidence/capture budget exceeded |
| 58 | Action may have applied; inspect evidence, never blindly replay |

The common CU interlocks keep their existing codes, including user abort **40**.
`cu doctor --json` publishes the exit-response contract.

## Verification

`make check` installs pinned JavaScript test dependencies and runs Python, Node,
lint, and package-build gates. The Node suite exercises JSDOM's DOM implementation and mocked browser APIs. These cannot prove Firefox permission behavior,
focus, rendering, or real site acceptance. Live acceptance requires temporary
installation and a nonsensitive fixture in an explicitly authorized browser
window, including revocation and negative controls.

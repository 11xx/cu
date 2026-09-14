# CU Browser for Firefox

CU Browser provides bounded page evidence and explicitly granted DOM interaction.
It requests only `activeTab`, `scripting`, and `nativeMessaging`: no all-sites
permission, TCP listener, or persistent page archive.

The supported and unsupported slices, their source/test evidence, and bounded
successor contracts are maintained in the [Firefox capability matrix](../docs/browser-capabilities.md).
Browser CSS rectangles remain unprojectable into CU frames; the association
probe and invalidation contract are in [browser-frame-association.md](../docs/browser-frame-association.md).

## Enable a tab

Run `cu web setup`, then temporarily load `browser-extension/manifest.json`
through Firefox `about:debugging`. Setup registers the native host; neither setup
nor loading the extension grants a page. The popup offers:

- **Read current tab**: page observations and condition waits.
- **Allow page interaction**: observations, DOM actions, and viewport capture.
  **Show amber browser cursor** optionally marks action targets inside the page.
- **Stop**: revoke the grant, cancel waits, remove the indicator, and disconnect
  the native helper.

A read grant cannot silently acquire interaction authority. Stop it before
choosing a different mode. Reload, navigation, moving a tab to another window,
tab closure, and native disconnect revoke the grant. Switching tabs never transfers it. Actions require the granted
tab to be active in the focused Firefox window; reads can describe a background
tab and label its activity separately from DOM visibility.

Temporary add-ons last for the Firefox session. Persistent installation requires
a separate Firefox distribution/signing workflow. No signing, upload, publication,
Chrome installation, or browser security-preference change is performed by CU.

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
loading, and the reloaded document has no authority until the user grants it
again. A tab that stops being current before dispatch, or a reload Firefox
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
resolution. Full-page and stitched capture are unsupported. A browser PNG has
no CU frame ID, reports `actionable: false` and `input_authority: "none"`, and
cannot authorize native coordinate input.

The optional cursor is an original 36-pixel amber dart: non-focusable,
`pointer-events:none`, and excluded from observations and freshness accounting.
It marks the latest DOM action target, not an OS-pointer movement. Stop,
revocation, and collector disposal remove it.

## Transport and refusals

The extension ID is `cu-browser-read@local`, the native host is `org.cu.read`, and
the protocol is `cu-browser-read`, version **5**. Extension and host versions must
match. Frames use native-endian uint32 length plus UTF-8 JSON, bounded at 1 MiB.
Missing grant capabilities mean read-only, never implicit action authority.

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

# Design

## Goal

Let a multimodal coding agent see and drive this desktop — pointer, keyboard,
windows, browser — through a deterministic CLI that any harness can call as a
shell command.

Claude Code and Codex are the initial consumers. Neither ships Linux computer
use, but both already run shell commands and read image files from disk, which
is the entire integration surface this design needs. Nothing below depends on a
particular harness or provider.

## Shape: a binary, not a prompt

Three layers, and almost all the weight sits in the first.

**`cu`, a single executable.** Everything a shell genuinely cannot do
correctly: coordinate transforms across mixed-scale outputs, `/dev/uinput`
device lifecycle, capture with a token budget, structured observation, and the
safety interlocks. No daemon. Subcommands, JSON on stdout, stable exit codes,
never interactive.

**The guide, carried by the same executable.** A bare `cu` prints the loop, the
exit-code contract, and the judgment around them; `--help` carries each
command's flags. There is no external skill or prompt fragment, because a
briefing that lives outside the tool is one a session reaching straight for
`cu click` never reads — and one that drifts from the behavior it describes
with nothing to catch it. If the guide starts accumulating rules like "remember
to divide by the scale factor", that rule belongs in `cu` instead.

**An optional MCP server**, later, for harnesses without a shell. Not needed
for either initial consumer.

The reason to put the work in a binary rather than a prompt is that the failure
modes here are silent. A model that mixes coordinate spaces does not error — it
clicks confidently on the wrong pixel. Determinism is the product.

## The frame contract

This is the core of the design; most of the rest follows from it.

Three coordinate spaces are in play, and conflating any two is *the* bug class:

- **Physical pixels** — what `grim` writes. Per-output, unscaled.
- **Logical coordinates** — what Hyprland speaks: window geometry, cursor
  position, cursor moves. One continuous space across all outputs.
- **Image pixels** — what the model actually sees, after cropping and
  downscaling to fit a token budget.

On this host the physical→logical ratio is 1.25 on one monitor and 1.0 on the
other, so no global constant relates them. See [environment.md](environment.md).

Every capture emits a **frame descriptor** alongside the PNG:

```json
{
  "id": "f3",
  "source": { "type": "output", "name": "DP-2" },
  "logical_rect": { "x": 0, "y": 0, "w": 2048, "h": 1152 },
  "image_size":   { "w": 1280, "h": 720 },
  "captured_at":  "2026-08-05T12:31:07Z",
  "cursor_logical": { "x": 173.0, "y": 627.4 }
}
```

A frame states only which logical rectangle it depicts and how many pixels it
depicts it with. The inverse transform is then one interpolation:

```
logical = logical_rect.origin + image_coord * (logical_rect.size / image_size)
```

Output scale, downscale ratio, crop offset, and monitor origin all collapse
into that single ratio. The same expression is correct for a full-output frame,
a window crop, and an arbitrary region — there is no second code path to get
wrong.

`logical_rect` is always whole pixels, rounded outward from whatever was asked
for and back inside the output. `grim -g` takes integers, so a fractional
rectangle — which is what a window-relative landmark resolves to — would be
captured as one region and described as another, and every coordinate mapped
back through the frame would inherit the difference.

**The model only ever emits image coordinates, always paired with the frame id
they came from:**

```
cu click --frame f3 --at 412,300
```

That pairing is what makes the whole thing safe. `cu` rejects a click against a
frame whose window has since moved, closed, or lost focus, and against a frame
older than a staleness threshold. The common catastrophic failure of pixel
computer use — acting on coordinates read off a screenshot of a different
monitor, or of a screen that has since changed — becomes a refused command
rather than a misdirected click.

## Input: Hyprland positions, uinput acts

A hybrid, split along the line where each mechanism is exact.

**Pointer position via Hyprland IPC.** `hl.dispatch(hl.dsp.cursor.move({x=…,
y=…}))` is an absolute warp in logical coordinates, taking floats. It needs no
calibration and no knowledge of output layout — Hyprland already owns that
mapping.

The exact call shape matters more than usual here, because four plausible
variants of it *silently do nothing while reporting success*
([environment.md](environment.md)). That is the failure mode this project is
built to eliminate, appearing in its own foundations, and it is why the wrapper
belongs in `cu` rather than in prompt guidance: a caller gets one spelling that
works, not five that look identical at the call site.

The alternative, a uinput absolute-axis pointer, would require mapping a device
axis range onto a multi-output desktop with unequal scales. That mapping is
compositor policy, not something we control, and it is exactly the fragile
surface this design is trying to eliminate.

**Buttons, scroll, and keys via `/dev/uinput`.** A virtual keyboard and pointer
created directly by the unprivileged process — no root, no `ydotoold`, verified
writable as uid 1000. Hyprland's key dispatchers are keybind-oriented and a
poor fit for free text.

So a click is: warp through IPC, assert the expected window is still under the
point, then press and release through uinput.

**A second keyboard path exists, for a sleeping desktop.** Genuine libinput
events are what make uinput injection indistinguishable from a human, and are
also why each one resets the idle timer and wakes every monitor. Hyprland's own
key dispatcher does not travel that path, so `--backend compositor` drives a
keyboard while the displays stay powered down. It is not the default: it
presents no input device to the client, and it cannot press a button or turn a
wheel, because the compositor exposes no dispatcher for either. Pointer actions
therefore stay on uinput and stay loud. Details and the measurements are in
[environment.md](environment.md).

**Text entry defaults to the clipboard.** `wl-copy` the text, send Ctrl+V, then
restore the previous clipboard contents. This is layout-independent and fast,
and it sidesteps building a keycode map for the active keymap. It has two known
failure modes — terminals with different paste bindings, and applications that
reject synthetic paste — so `cu type --method keys` falls back to per-character
keycode synthesis, and the type-and-verify check below catches the rest.

## Observation is structural first, visual second

Hyprland exposes a live window model: class, title, geometry, PID, focus,
workspace, floating state, XWayland status. That is semantic ground truth of
the kind that X11 and macOS computer use only get from accessibility APIs, and
most implementations do without.

`cu observe --json` returns monitors (with scale and logical rects), windows,
workspaces, the focused window, and the cursor position. It is exact, costs a
few hundred tokens, and answers most of what a screenshot is usually asked to
answer — which window is focused, whether the dialog opened, where the terminal
is.

The design consequence is that **a screenshot is for reading content, not for
locating windows.** An agent should call `observe` freely and `shot` when it
actually needs pixels. This is the largest single lever on both token cost and
reliability, and it exists only because the compositor is scriptable.

`cu wait` subscribes to the Hyprland event socket for the same reason: waiting
for `openwindow` beats screenshotting in a loop. It also answers from a
snapshot first, because a window that is already open would otherwise block
until the timeout — the most confusing possible answer, since the window is
right there.

The order of those two is load-bearing: **subscribe, then check.** The
compositor keeps running between them, and an event announced in that gap
reaches neither the snapshot nor a socket not yet listening. Checking first
costs a wait its whole timeout and reports that the thing never happened, with
the thing on screen. Subscribing first makes losing the race free — the event
is already buffered when the snapshot comes back empty.

## Capture and the token budget

Per-output capture is the primitive; whole-desktop capture is never used
because Hyprland composites it at the largest scale in play, upscaling the
unscaled monitor into a larger and blurrier image.

Even so, one native output is 2560x1440 — near the per-image ceiling on its
own, and the logical desktop is nearly three times that. Capture is therefore
budgeted rather than literal:

- `cu shot --window active` crops to one window. Usually the right default:
  fewer tokens *and* more detail per token than a full output.
- `cu shot --region <x>,<y> <w>x<h>` captures an arbitrary strip.
- `cu shot --output DP-2` for a whole monitor.
- `--for <consumer>` selects a capture profile, which sets the downscale
  target. Profiles are configuration, not code — see below.
- `--if-changed` hashes the region and skips re-sending an unchanged screen,
  which is the common case in a wait loop. The reused frame is re-dated by the
  capture that confirmed it: the pixels were just read back and matched, which
  is the evidence the staleness threshold stands in for, so a frame proven
  current must not then be refused as too old.

A profile describes a consumer by **capability rather than by one number**,
because consumers do not share a unit. An image is billed in whole square
patches, and the patch is 28px on one side and 32px on another, so a single
long edge cannot express both — and a model that caps by patch count rather
than by pixels has no long edge to state at all. A profile therefore carries
the long edge to downscale to, the ceiling the consumer accepts, the patch size
it bills in, its own budget, and the per-image limits, and `cu` computes what a
capture costs rather than the agent doing arithmetic about it.

Every profile inherits one base, `generic`, and states only what it changes.
That is what keeps a new consumer a stanza under `[capture.profiles]` instead
of a change here — and it makes the base load-bearing: it holds the most
conservative reading of each field, so an unmeasured consumer is over-priced
rather than under-priced, and a typo resolves to a refusal rather than to a
silently ignored key.

The shipped profiles record measurements, not preferences, and the measurement
belongs in the config comment beside each. `claude` downscales to 1080p because
that is both the recommended balance for computer use and the largest capture
the high-resolution tier accepts without downscaling it again; returned
coordinates map 1:1 to image pixels there, so no scale-factor correction is
needed on the model's output, only the frame transform above.

Frames are written to disk and the path is printed with the descriptor. Both
Claude Code and Codex read image files natively, which makes the filesystem the
provider-agnostic image channel — `cu` never talks to a model API.

### Aimed capture, and its failure mode

`grim -g` takes logical coordinates — the same space as `logical_rect` — so a
frame's rect *is* its capture argument, with no conversion between requesting a
region and describing it. An agent that knows where to look can capture a
status bar or a single dialog line for a few dozen tokens instead of a few
thousand, and read it at full resolution rather than downscaled.

The precondition is knowing where to look, and that is what `observe` supplies:
regions are derived from live window geometry at capture time, not from the
model's recollection of where something was a few turns ago. A window that
moved yields a correctly-aimed strip rather than a confidently wrong one.

The failure mode is real, though, and it is worse than a wasted screenshot: a
narrow crop aimed at the wrong place returns a perfectly sharp image of
something irrelevant, and nothing in the picture says so. Wide captures fail
loudly — you see the wrong screen. Narrow ones fail quietly. Three things keep
that in check:

- **Frames are self-describing.** The descriptor states which window and which
  rectangle the image depicts, so a 200x40 strip arrives labelled as a strip of
  a named window rather than as "the screen".
- **Widening is the cheap move.** `--window` then `--output` are always
  available, and the guidance for the harness layer is to widen on the first
  surprise rather than re-aim repeatedly. Two aimed captures that disagree with
  expectation cost more than one full-output capture.
- **`cu` refuses degenerate aims** — regions spanning outputs (which upscale),
  and regions falling outside any window's current geometry.

Aimed capture is an optimization over a working default, not the default
itself. `--window active` stays the recommended first call.

## Safety

"Whatever works here, safely" is the requirement that most shapes this design,
because the target is a live desktop with the user sitting at it.

**Visible by default, and never headless by accident.** Every command aims at
the screen the user is looking at: a bare `cu shot` captures the focused window
on a real monitor, and nothing selects a headless output on its own. This is a
safety property, not an implementation detail — a person watching an agent
drive their desktop can see it going wrong and stop it, which is the cheapest
and fastest interlock in the system, and the only one that needs no code to
work. Somewhere invisible is where an agent's mistakes get expensive.

Headless outputs remain the right place to *develop* the input path, so they
are reachable through `cu sandbox create` — a command that names what it is
doing — rather than by improvising `hyprctl output` calls. `cu sandbox remove`
refuses anything but a headless output, because the compositor's own command
would take away a physical monitor and blank the desk.

The same reasoning bounds the compositor keyboard backend. Working while the
displays sleep is useful and it is also, by construction, working where nobody
can see; that is why it is opt-in per command and never the default.

**Assertion at injection time, on whichever question the command depends on.**
Every input command re-checks the world immediately before injecting rather
than when the action was planned, but the two kinds of command depend on
different facts and asserting the wrong one forbids ordinary work.

- **Keyboard commands assert focus.** `type` and `key` go wherever focus
  happens to be, so the window holding it must be the one the agent expected.
  The dangerous race is the user alt-tabbing mid-action and receiving a
  password into the wrong application.
- **Pointer commands assert the target.** A click is aimed by coordinates, and
  demanding its target already hold focus would forbid clicking a window *to*
  focus it — which is most of what clicking is for. What a frame guarantees
  instead is that the window under the point is still the one it depicted, so
  a window that opened over the target since capture refuses the click rather
  than swallowing it.

Both refusals report `FOCUS_CHANGED`, because the caller's response is the
same: re-observe, then decide again.

**Denylist.** Refuse to inject into window classes matching a configurable
list — lock screens, password managers, authentication prompts. Refuse
entirely when the session is locked.

The lock-screen and remapper guards both read `/proc`, and the scan is dated
rather than fixed at process start. One scan serves both within a single
injection — they are meant to describe the same instant — but it expires
quickly, because a replay injects repeatedly over minutes and a session locked
partway through has to be seen by the actions that follow it.

**Guaranteed release.** Every press is paired with a release, including on
`SIGINT`/`SIGTERM` and on panic. Destroying the uinput device releases its keys
at the kernel level, which makes process death safe by construction, but the
handler is explicit rather than relying on that.

**A user abort the agent cannot override.** A Hyprland keybind the user presses
to drop a lockfile; `cu` checks it before every injection and refuses while it
exists. The agent has no way to clear it.

**Human-presence detection.** After warping, `cu` re-reads the cursor position;
a mismatch means the user is moving the physical mouse. Yield rather than
fight for the pointer.

**Append-only action log**, with `--dry-run` producing the same log without
injecting.

**Verify what was typed.** `cu type --verify` selects all, copies, and compares
the field against what was sent. Silent partial input is the failure mode that
the clipboard and keycode paths share: a paste an application rejects and a
synthesized run whose leading characters were dropped both look exactly like
success at the call site.

Three outcomes, and the third is why this is worth having. The comparison is
containment rather than equality, since select-all returns whatever else the
field held. A field that cannot be read back reports **unverified** rather than
ok — claiming a verification that did not happen would be worse than not
offering one. A genuine disagreement is `VERIFY_FAILED`, which is deliberately
*not* retryable: retyping blindly duplicates whatever did land.

Verifying is not free of consequence: it leaves the field's contents selected,
so the next keystroke replaces them rather than appending. `cu` does not
collapse the selection afterwards, because the key that would do it means
something different in every widget and injecting an unasked-for keystroke to
tidy up is the guess this project refuses everywhere else. Submit after
verifying, or re-aim before typing again.

## Testing without touching the user's screen

**Verified.** `hyprctl output create headless` adds a real output the
compositor treats like any other; applications launch onto it, `grim -o`
captures rendered content from it, and removing it restores the session
byte-identically. Details and measurements in
[environment.md](environment.md).

So the test suite drives real applications on a monitor that does not
physically exist — real compositor, real input path, real capture, and no
possibility of stealing focus or clicking something on a screen the user is
looking at. That is a better safety story than any amount of care taken while
driving live outputs, and it makes the input stage developable at all.

It also lands at scale 2.00, a third distinct scale alongside the host's 1.0
and 1.25, so coordinate math gets exercised against a case neither real output
covers.

## Browser control

The largest module, and the one whose shape is dictated by a constraint rather
than a preference.

### The constraint is automation detection, not authentication

A logged-in session on a driver-controlled browser gets challenged anyway:
correct credentials, correct 2FA, no VPN, and Cloudflare still captchas every
page. Authenticating harder does not help, because what is detected is the
automation, not the identity.

Two consequences follow, and they set the whole design:

- **Driving a separate automation browser is the wrong default.** It is the
  configuration that gets blocked, *and* it strands the user's real session —
  asking someone to log in again, in a browser they don't use, to let an agent
  change a setting in an account they are already signed into, is a bad trade
  even when it works.
- **The real browser, with the real profile, is the target.** Real cookies,
  real profile, real fingerprint, launched the ordinary way.

### Act with real input, read with a side channel

The split that satisfies both goals:

**Acting is always synthesized input** — the uinput device and cursor warp
already built for the rest of the system, aimed at the user's normally-launched
browser. These are kernel-level input events travelling the ordinary path
through libinput and the compositor, so at the browser they are genuine events
rather than protocol-injected ones. There is no automation protocol in the
acting path at all.

**Reading is a side channel** used only to *locate* things — element boxes,
text, state — so the real input can be aimed. The read channel never performs
the action.

This inverts the usual driver design, where the protocol both finds the button
and clicks it. Here it only finds it.

Behavioral fingerprinting is a separate axis and is not solved by this: warping
instantly between coordinates is itself a signal. Interpolated movement with
plausible timing is a `cu` option rather than a browser-module concern, since
it belongs to the input layer.

### Read tiers, ordered by footprint

Each tier is more informative and more conspicuous than the one above it. The
module uses the least conspicuous tier that answers the question.

1. **Pixels only.** Screenshot plus the model's own vision. Zero footprint,
   works in every browser, needs no flags and no launch changes. Always
   available as the floor.
2. **Accessibility tree (AT-SPI).** Both engine families expose roles, names,
   and element bounding boxes to assistive technology over D-Bus. This is not
   an automation protocol — it is the interface screen readers use, so its
   presence is an ordinary property of a real user's desktop rather than a bot
   signal. If the coordinates it reports can be mapped into logical space, this
   is the highest-value tier for this design, and it would upgrade every GUI
   application, not only browsers.
3. **CDP or WebDriver BiDi.** Richest: DOM, precise geometry, real load events.
   Also the most conspicuous, and it requires launch flags. Reserved for sites
   that do not fight automation, and never used for input.

### Chrome is driven by resolved actions, content by pixels

Zen, Vivaldi, and Helium have heavily customized interfaces, and Vivaldi can
run with its window decoration toggled off entirely. Pixel-hunting for a URL
bar across six browsers with divergent and user-reconfigurable chrome is
unwinnable, so chrome is operated by keyboard and only page content by
coordinates.

**But the keyboard is not the stable layer — on this desktop it is the most
customized one.** Keys pass through an Emacs remapping layer and then through
each browser's own bindings before reaching an action, and the result is not
predictable from defaults: focusing Zen's URL bar is `Ctrl+Alt+Shift+F4`,
closing a tab is `Ctrl+Alt+Shift+Delete`, while opening a tab is still plain
`Ctrl+T`. Some bindings are customized, some are not, and nothing about a
chord's ordinariness predicts which. The full pipeline is in
[environment.md](environment.md).

So the design treats **the keymap as configuration to be read, never a constant
to be assumed**:

- `cu` exposes *semantic actions*, not chords: `cu browser focus-urlbar`,
  `cu browser close-tab`. The agent never names a key combination.
- The registry resolves each action to a chord **per browser, from that
  browser's own configuration** — `customKeys.json` for the Gecko family,
  whatever the equivalent is elsewhere — and re-reads it when the file changes.
- An action with no known mapping is **refused, not guessed**. Falling back to
  a documented default is the failure mode this whole section exists to
  prevent: it would silently perform some other action rather than erroring.
- Because injected keys enter after the remapping layer, the chord `cu` emits
  is the one the browser listens for, not the one a human presses.

The chrome-versus-content split survives, then, but for an inverted reason. It
is not that keys are stable and pixels vary. It is that the keymap is
*knowable from configuration* while chrome layout is not — so the part that
varies per browser is still the part that never gets clicked, and the part that
varies per user is read from disk rather than assumed.

This generalizes past browsers. Any application may rebind anything, so `cu
key` takes semantic names wherever a mapping is known and never assumes an
application's defaults.

### The browser registry

Per-browser facts, discovered rather than assumed: executable path (resolved
through desktop entries as well as `PATH`, since Helium has no `PATH` entry),
engine family, window class for focus assertion, which read tiers are available
and how to enable them, and quirks such as Vivaldi's optional decoration.

### What has to be measured before it is trusted

The stealth claims above are hypotheses with a known counter-example — driver
automation on this host demonstrably got blocked — so they get tested rather
than asserted. The probe is to load a site known to challenge automation under
four configurations and compare outcomes: the real browser untouched; the real
browser with a debug port open; the real browser driven by synthesized input;
and a driver-launched browser as the known-bad control.

Two specific unknowns feed into it. Recent Chromium versions restrict remote
debugging against the default profile directory, which may make tier 3
incompatible with using the user's real Chromium profile at all. And enabling
the Gecko remote agent may set the `navigator.webdriver` flag, which is the
single strongest tell there is. Both are cheap to check and both would change
which tiers are viable per family.

## Recipes: turning a successful run into a repeatable one

Driving a site well once is worth keeping. The natural requests are "you did
that well, make it something you can repeat" and "you got that wrong last time,
do it differently" — and both need memory that outlives the session.

The append-only action log the safety design already keeps is the raw material:
a successful run is a sequence of `cu` commands that worked, and promoting it
to a named recipe is mostly a matter of parameterizing it and recording what it
was for.

**Storage is user-global, not per-project.** These are facts about a website,
not about a repository — the same login flow is worth reusing from any project.
The arc journal is deliberately per-project and is the wrong home. Recipes live
under `$XDG_DATA_HOME/ai-computer-use/recipes/`, keyed by site and task.

A recipe records its steps, its preconditions, a verification step that decides
whether a replay actually worked, and a revision history carrying outcomes.
That history is what makes the second request answerable: "do it differently"
resolves to a superseded revision plus the recorded failure, so the next
attempt starts from what went wrong rather than from nothing.

Pointer steps retain both a window-relative position and the window size they
were measured against. The fraction survives the window moving; it does not
say how a control follows a resize. Replay therefore refuses material size
drift and asks for the procedure to be recorded and reviewed again. For a
partially off-screen window, the logical target is mapped into the visible
capture rather than multiplying the fraction by the cropped image dimensions.

**Recipes are generated code that runs against a real authenticated browser**,
which makes them a security boundary rather than a cache. They are reviewable
before first replay, stored with a content hash, and re-reviewed when that hash
changes. A stored recipe must never be able to change silently.

## Learned knowledge: cutting the second visit down

Finding a control costs a full-window capture, a model's attention, and often
several attempts. Keeping that is worth as much as keeping a successful run:
the second visit to a site should start from "the search box is about here"
rather than from nothing.

This is a different thing from a recipe, and the difference is what makes it
safe. A recipe is a whole procedure, human-reviewed, replayed as a unit.
Knowledge is a *fact* about an application — where a control sits, which chord
an action needs, that a dialog must be dismissed before anything else responds
— written automatically by whatever ran last, with nobody reviewing it.

**So a landmark says where to look, never where to click.** It resolves to a
capture, not to a coordinate an injection may use. `cu shot --landmark
zen/urlbar` derives a region from the window's geometry *now* and the model
reads those pixels before anything acts on them. Knowledge collapses the
search; the frame contract still decides the coordinate.

That boundary is the entire argument. An ungated store that could aim a click
would reintroduce precisely the failure this project exists to prevent — a
remembered position whose control has moved, clicked confidently, landing on
something and reporting success.

### A fraction survives a move, not a resize

Positions are stored window-relative for the same reason recipes store them
that way. But a fraction only survives a *resize* if the control scales with
the window, and chrome does not: a toolbar is a fixed number of pixels tall at
any window height. Under a tiling compositor that is the ordinary case rather
than the exception — the same application is a full screen, a half, and a third
within one session — so a stored fraction goes wrong by the ratio of the two
sizes, systematically, and silently.

So a landmark carries the window geometry it was measured at, and resolving it
projects the fraction under each way the control could have followed the
change: scaling with the window, or holding its pixel size against the near or
far edge. The region covers whichever of those the current window still admits
— a projection landing outside the window refutes itself and is dropped — and
it widens as they diverge. A run that knows how a control behaves can name the
anchor per axis and buy back the precision; unnamed is the default, because the
run that measured a fraction usually cannot tell either.

Past the point where covering them honestly would take most of the window, the
landmark reports itself **stale** and yields the whole window: the search it
was supposed to collapse is no longer collapsed, and saying so costs one
ordinary capture where aiming somewhere plausible costs a wrong answer.

That is the shape of the store as a whole. **Knowledge steers; it does not
assert.** The desktop it describes is too dynamic to support an assertion, and
the two failures are not symmetric — a hint that hedges costs a slightly larger
capture, while a hint that claims precision it does not have costs a confident
mistake. Every part of `know` is built for the first cost.

Each landmark also carries how often it was confirmed or missed. A landmark
that keeps missing belongs to an application that changed, and the counters
make that visible rather than letting it quietly cost an extra capture on every
run.

## Telling the user, while it happens

An agent driving a desktop is the most visible thing on it and the easiest to
miss. Being watchable is already a safety property here — it is the argument
for acting on the real screen rather than a headless output — but watchable
only pays if the person can tell *that* it is happening without staring.

**`cu` publishes state; it does not draw.** The indicator belongs in whatever
the user already looks at, which on this desktop is the status bar.

That split is not only taste. An overlay and a screenshot-driven agent compete
for the same pixels: `grim` captures layer-shell surfaces, so anything `cu`
drew would land in the frames the model then reads and acts on. Hyprland 0.56
offers no exclusion — a `noscreenshare` layer rule is accepted and does
nothing — which leaves hiding the overlay around every capture, or editing it
back out of the PNG. The status bar is already a layer surface in every frame,
so an indicator inside it adds no new interference at all.

Two artifacts, because there are two questions:

- **`$XDG_RUNTIME_DIR/cu/status.json` — what is happening now.** One small
  object, replaced atomically. A bar polling it gets a whole state or the
  previous whole state and never parses history to find the end of it. Runtime
  state, so a reboot cannot leave a stale claim behind.
- **The action log — what happened.** Unchanged, append-only, read afterwards.

**It is a registry, not a record.** Nothing stops two harnesses from driving
one desktop, and a single last-writer-wins record answers the user's question
wrongly in exactly that case: the second agent's first command erases the
first's, and the bar shows one agent while two are typing. Each driver is an
entry keyed by its session, so the answer is a list and the bar shows how many
things there are to reckon with. Mutations take a lock: atomic replacement
keeps a *reader* whole, but does nothing to stop two writers building a new
registry on the same stale copy and one of them winning.

**A session is named, never inferred.** With two agents present, deriving which
stretch of work a command belongs to from process ancestry is a guess, and a
wrong guess files one agent's action under the other's intent — which is worse
than an unbracketed entry, because it reads as true. `cu session begin` prints
an id and later commands claim it through `CU_SESSION` or `--session`.

**Entries expire, one at a time.** `cu` has no daemon, so nothing writes
"finished" for a process killed mid-action. Each entry carries the moment it
stops being true and a reader drops it past that. A stuck "an agent is driving
your desktop" is the one failure that would teach the user to ignore the
indicator.

A session's entry expires on a timeout it renews with every command inside it,
because the gap between two commands is a model thinking rather than an agent
that stopped. An unbracketed command instead watches its own process and is
gone the moment that exits, which is all it honestly claims: each `cu` is a new
short-lived process, so a timeout would leave one entry behind per invocation
and grow a row of marks for work that finished seconds ago.

**`--why` is the part worth showing.** `cu click` says an agent is acting;
`--why "clicking Submit"` says what it is trying to do, which is what decides
whether to interrupt. `cu session begin --why "booking a flight"` gives a
bracket its standing purpose; later actions use their own `--why` intent. The
bracket keeps the indicator from blinking out while a model spends longer
thinking than one action's expiry.

**The abort flag rides along.** The user presses their abort and each agent
stops at its next command. Without it in the payload the bar would show nothing
changing in between, at the one moment they most need to see that their stop
landed — and an indicator would have to know where the flag lives.

`cu status` prints one line per driver and nothing at all when nobody is
driving, which is exactly what a bar module wants to render for idle:

```jsonc
// waybar
"custom/cu": {
  "exec": "cu status --json",
  "return-type": "json",
  "interval": 1,
  "format": "🤖 {}"
}
```

`eww/` carries a ready widget — `cu.yuck` and `cu.scss`, structure only, with
colour left to the configuration that includes them.

## One desktop, one driver at a time

Every other interlock asks whether *this* action is safe. None of them can see
the question a second agent raises, and both failures it produces are silent.

`cu click` warps the pointer, then presses. It re-reads the pointer immediately
before the button event, which catches the user reaching for the mouse — but a
second `cu` warping in that same window is indistinguishable from nothing
having happened, and the click lands, at exit 0, on whatever is now under the
cursor. That is the failure the frame contract exists to prevent, arriving
through a door the frame contract does not cover. Focus and the clipboard are
shared the same way: two `type` commands interleave their keystrokes into
whichever window has focus, and each restores the clipboard the other saved.

So the injecting commands are serialized host-wide by `flock` on
`$XDG_RUNTIME_DIR/cu/desktop.lock`. Three properties matter and all three come
from the kernel rather than from this code: the lock is between *processes*,
the only scope that means anything for a one-shot CLI; it is released when the
holder exits, so an agent killed mid-action cannot wedge the desktop; and it
needs no daemon to arbitrate.

Waiting is bounded. Two agents reaching for the desktop at nearly the same
moment is ordinary traffic and worth absorbing; one holding it for longer is a
fact the caller needs told rather than a stall to hide. Past the wait the
answer is exit 42 naming the holder — retryable, because unlike an abort it
describes someone else's work in progress rather than the user's refusal.

Reading is never serialized. `observe`, `shot`, `wait`, `status`, and `doctor`
answer while someone else is driving, which is how both the other agent and the
user find out what is going on.

## Command surface

The whole contract, in one place. Everything takes `--json` and `--dry-run`.

| Command | Purpose | Creates a uinput device |
| --- | --- | --- |
| `cu status` | What cu is doing, for a status bar to poll | no |
| `cu session` | Bracket a stretch of work as one activity | no |
| `cu doctor` | Verify every precondition in [environment.md](environment.md) | no |
| `cu observe` | Monitors, windows, workspaces, focus, cursor | no |
| `cu shot` | Capture; writes a PNG and a frame descriptor | no |
| `cu wait` | Block on a Hyprland event (`openwindow`, focus change, title match) | no |
| `cu focus` | Give a window keyboard focus | no |
| `cu move` | Warp the pointer to a frame coordinate | no |
| `cu click` | Warp, assert focus, press/release | yes |
| `cu scroll` | Wheel events at a frame coordinate | yes |
| `cu type` | Text entry, clipboard by default | yes |
| `cu key` | A semantic action, or a literal chord | yes |
| `cu browser` | Semantic browser actions, resolved per browser | yes |
| `cu recipe` | Save, list, review, and replay a stored procedure | via replay |
| `cu know` | What past runs learned about an application; aims captures only | no |
| `cu sandbox` | Create and remove headless outputs, explicitly | no |

`observe`, `shot`, `wait`, and `focus` never touch the input path at all, which
is what makes the read-heavy loop cheap and safe. `focus` earns its place there
because `type` and `key` act on whatever holds focus, and without it an agent's
only way to redirect them would be a click — which needs a frame and moves the
pointer as a side effect.

### Measured cost per invocation

| Stage | Cost | Notes |
| --- | --- | --- |
| `uv` + interpreter start | ~28 ms | Warm cache; paid by every subcommand |
| uinput device construction | ~100 ms | Input subcommands only |
| Compositor enumeration | ~6 ms | Hyprland sees a new device quickly |
| Device teardown | ~6 ms | |

So a read command costs about 30 ms plus whatever `hyprctl` or `grim` takes,
and an input command about 135 ms. That is comfortable enough that **the
daemonless design holds** — the earlier worry that per-invocation device
creation would force a resident process does not survive measurement.

Two consequences do follow. **Batching matters**: `cu type` of a long string
pays the device cost once, whereas fifty separate `cu key` calls pay it fifty
times, so composite operations belong inside one invocation.

And the settle delay turned out to be **load-bearing rather than removable**.
Building the device costs about 30 ms of ioctls; the rest is a deliberate pause
before the first event, and cutting it silently loses the *leading* keystrokes
of every command. Typing a marker immediately after construction, four trials
per setting:

| Settle | Markers arriving intact |
| --- | --- |
| 0 ms | 0 of 4 |
| 5 ms | 0 of 4 |
| 10 ms | 2 of 4 |
| 20 ms | 3 of 4 |
| 40 ms | 4 of 4 |

The kernel accepts those events and the compositor discards them, because it
has not finished enumerating the device — so the failure is silent, partial,
and looks like an application ignoring input. 40 ms is the first value with no
losses and is the shipped default, tunable as `input.settle_ms`. This is the
single clearest argument against shaving per-invocation cost by reflex: the
100 ms was doing something.

## Seams to resolve before building

The design fixes the architecture but leaves four mechanisms unspecified. Each
is small, and each is load-bearing enough that guessing it during
implementation would be a rewrite.

**The frame store.** `--frame f3` is state that outlives a process, and there
is no daemon to hold it. So frames live on disk under
`$XDG_RUNTIME_DIR/cu/frames/` — tmpfs, `0700`, cleared on reboot, which suits
data that is worthless once the screen has changed. Still to fix: how ids are
allocated, the staleness threshold past which a frame is refused, and when old
frames are collected.

**The exit-code taxonomy.** "Stable exit codes" is promised but never
enumerated, and the agent's behaviour depends entirely on which refusal it got.
A stale frame means *re-capture and retry*. A focus change means *re-establish
context*. The user's abort means **stop, and do not retry** — an agent that
treats that as transient defeats the safety mechanism. These must be distinct
codes with distinct documented responses.

No failure leaves by another door. Every error the package declares maps to a
code, and anything unanticipated still exits `ERROR` with its type named on
stderr rather than as a traceback — a caller whose whole contract is the exit
code has nothing to branch on otherwise.

**Configuration.** Settled: `$XDG_CONFIG_HOME/cu/config.toml`, defaults shipped
in code so a host with no file still behaves correctly, user tables merged over
them. Tables are merged recursively but lists are replaced wholesale — a user
who shortens the denylist means it, and a merge that silently re-added entries
would be impossible to reason about.

The keymap lives there too, keyed by window class with `*` as the fallback
entry. Only bindings that are genuinely near-universal belong under `*`: paste
qualifies, and terminals are its known exception, which is why they are
enumerated rather than guessed at.

An application's table can also state that it has *no* binding for an action,
by giving it an empty chord. That stops the fallback rather than falling
through it, and the distinction matters: a terminal that named only its paste
and copy chords would inherit `ctrl+a` and `ctrl+z` from `*`, where they are a
shell's beginning-of-line and a suspend signal. An unmapped action refuses; an
inherited one performs something else and reports success.

**The abort mechanism.** The lockfile is `$XDG_RUNTIME_DIR/cu/abort` — session
state that must not survive a reboot. `cu abort` reports its state, and gives
the user `--set` and `--clear` commands so every consumer names the interlock
rather than its private path. Calls marked as agents by `CU_SESSION` or
`--session` refuse either mutation with exit 43. That prevents an injection
path, retry, or instruction-following agent from changing it by accident; it
cannot stop a determined agent from bypassing the command.

`escape` carries no binding at all in this session's keymap, so the abort takes
the modifier shape already used for display control:

```lua
bind("SUPER + ALT + escape", hl.dsp.exec_cmd("cu abort --set"),
     { locked = true })
bind("SUPER + CTRL + ALT + escape", hl.dsp.exec_cmd("cu abort --clear"),
     { locked = true })
```

Two properties matter. `locked = true` keeps the abort reachable while the
session is locked, which is when an unattended agent is least supervised. And
clearing it is a *different chord* rather than a toggle: a toggle pressed twice
in a panic would re-enable the thing being stopped.

## Implementation

Python with PEP 723 inline dependencies, run through `uv` — the measurements
above were taken that way, so the approach is exercised rather than assumed.
`uinput` is struct packing into a file descriptor and capture is subprocess
orchestration; neither argues for a compiled language, and per-invocation
overhead is ~28 ms.

The tradeoff is honest: a Rust `cu` would be a single distributable artifact,
which matters if this is ever shared. Python is a reversible choice for now
because the CLI contract, not the language, is what the harness layer depends
on.

### Build order

Each stage is independently useful, and each is a precondition for the next.

1. **`doctor`** — encodes the environment checks and fails loudly when a
   precondition drifts. Nothing else is trustworthy without it.
2. **`observe`** — pure reads, no risk, and immediately useful to an agent on
   its own.
3. **`shot` plus the frame store** — establishes the contract everything else
   references. Worth getting exactly right before anything consumes it.
4. **Input** — `move`, `click`, `type`, `key`, with the safety interlocks
   landing *with* the first injection rather than after it.
5. **Browser and recipes** — the largest module, and the one still gated on the
   fingerprint probe.

The headless-output probe that gated stage 4 is done, and passed — input is
developable against a screen the user is not sitting in front of.

## Open questions

1. **Fingerprint probe.** The four-configuration test above settles which read
   tiers are viable per engine family. It needs a site that reliably challenges
   automation and a willingness to burn a few challenges on the account used.
2. **AT-SPI coordinate space.** Whether the accessibility tree reports usable
   coordinates under Wayland with mixed output scales decides whether tier 2 is
   the backbone of the read path or a footnote.
3. **Behavioral fingerprinting.** Instant warps between coordinates are a
   signal in their own right. Interpolated movement with plausible timing
   belongs to the input layer rather than the browser module, and is unbuilt.

## Shelved: a Hyprland plugin

A native plugin could expose exactly the primitives this needs — precise input
injection, geometry, and event hooks — without shelling out to `hyprctl` per
call, and without the silent-failure surface the Lua dispatchers turned out to
have.

It stays shelved deliberately. A plugin is compiled against Hyprland's internal
ABI and breaks on compositor updates, which is a standing maintenance cost that
the current approach does not carry. The standard routes are not exhausted yet,
and the measured per-invocation costs are comfortable. Revisit if injection
latency becomes the bottleneck, or if a needed primitive turns out to have no
IPC equivalent at all.

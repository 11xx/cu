# ai-agent-computer-use

Computer use for AI coding harnesses on Linux/Wayland, targeting Hyprland.

Gives a multimodal agent the ability to see the screen and drive the pointer,
keyboard, and windows of a real desktop session, through a deterministic CLI
that any harness can call as a shell command. Claude Code and Codex are the
initial consumers; nothing in the interface is specific to either.

## Install

```sh
make install                         # puts `cu` on PATH
```

An editable install, so the command on PATH is this checkout rather than a copy
of it — a stale binary is not a failure mode this project should have. It also
means the checkout has to stay put. Without installing, `bin/cu` is the same
command and needs no knowledge of `uv`; every example below works either way.

## Orientation

```sh
cu                                   # the workflow guide; no arguments needed
```

A bare `cu` prints what a session needs before its first command: observe
before capturing, act through the frame you read, what each exit code obliges,
how hard the driving model should think, and which interlocks exist. `--help`
is the per-command reference and points back at it. There is no skill or prompt
fragment to install alongside — the CLI is the only workflow surface, so a
caller that reaches straight for `cu click` can still find out what it owes.

## Usage

```sh
cu doctor                        # verify every environment precondition
cu observe                       # monitors, windows, focus, cursor
cu shot                          # capture the focused window
cu shot --output <name>          # a whole monitor; `cu observe` names them
cu shot --region '100,100 400x200'
cu shot --if-changed             # reuse the last frame if nothing moved
cu shot --after f3 --watch '0,0 200x26' # recapture once pixels change and settle
cu shot --for openai             # capture at another consumer's budget
cu pixel --frame f3 --at 412,300 # exact r,g,b,a and #RRGGBBAA from stored pixels
cu magnify --frame f3 --scale 3  # nearest-neighbour derived frame; no recapture
cu profiles                      # what a capture costs each consumer

cu wait window-open --class zen  # block on a compositor event
cu focus zen                     # give a window keyboard focus
cu move  --frame f3 --at 412,300 # warp the pointer to a frame coordinate
cu click --frame f3 --at 412,300 # warp, check the target, press and release
cu scroll --dy -3                # wheel where the pointer already is
cu type 'hello'                  # clipboard by default, --method keys to synthesize
cu type 'hello' --verify         # read the field back and confirm it arrived
cu key --action paste            # a semantic action, resolved per application
cu browser list                  # installed browsers and what each resolves
cu browser focus-urlbar          # a semantic browser action, never a chord
cu recipe save open-url --last 4 # promote a successful run from the log
cu recipe replay open-url --param text1=example.com

cu know set zen urlbar --rect 0.1,0.02,0.6,0.04   # remember where to look
cu know host 'keys pass through a remapping layer'  # a fact about the machine
cu shot --landmark zen/urlbar    # aim a capture, widened as the window drifts
cu sandbox create                # a headless output, when you want one

export CU_SESSION=$(cu session begin --why 'booking a flight')   # tell the user
cu click --frame f3 --at 412,300 --why 'accepting the fare'
cu status                        # a line per driving agent; empty when idle
cu session end
```

Every injecting command takes `--dry-run`, which runs every interlock and
writes the same log entry without emitting a single event.

`cu` acts on the **visible desktop** by default — a bare `cu shot` captures the
focused window on a real monitor, and nothing selects a headless output on its
own. Being watchable is a safety property: `cu sandbox` exists so that working
where nobody can see is a deliberate, named act.

All accept `--json`. `bin/cu` is a plain command — it resolves `uv` itself, so
a caller needs no knowledge of the Python underneath.

Capture budgets are configuration. A **profile** describes a consuming model by
capability — the long edge to downscale to, the patch size it bills in, its own
cap — and every profile inherits `generic`, stating only what it changes. So
teaching `cu` about a new consumer is a stanza under `[capture.profiles]`, and
a field nobody has measured falls back to a conservative default rather than a
guess. `cu profiles` shows what each resolves to; `--for` overrides per command.

`shot` writes a PNG plus a **frame descriptor** recording the logical rectangle
the image depicts, its pixel size, and what it costs the consumer it was
captured for. Later commands take coordinates as
`--frame <id> --at x,y` in image pixels, and `cu` maps them back to the screen.
`pixel` reads an exact stored pixel, or an explicitly requested median box,
without producing another image. `magnify` makes a nearest-neighbour derived
frame for reading small details; it preserves the source capture time and
logical rectangle while recording its larger image size and token cost.

`cu` publishes what it is doing to `$XDG_RUNTIME_DIR/cu/status.json` — one
small object, replaced atomically, which `cu status --follow --json` turns into
a change-only feed for a status bar. It does not draw an overlay: `grim` captures
layer surfaces, so anything `cu` drew would end up in the frames the model
reads. Entries expire, because nothing writes "finished" for a killed process.

More than one agent may be driving one desktop, so that object is a registry
with an entry per driver rather than a single record, and `cu status` lists
them all. A session is claimed explicitly — `cu session begin` prints an id
that later commands pass through `CU_SESSION` or `--session` — because
inferring it from process ancestry would file one agent's actions under
another's intent.

```sh
export CU_SESSION=$(cu session begin --why 'booking a flight')
cu click --frame f3 --at 412,300 --why 'accepting the fare'
cu session end
```

Add `--exclusive` to `session begin` when the desktop must remain unchanged
between those commands. While that session is live, only injecting commands
that name its id through `CU_SESSION` or `--session` may act; other injectors
get retryable exit 44 naming the reservation. Ordinary sessions reserve
nothing, and observation remains concurrent.

The commands that reach the shared pointer, keyboard focus, and clipboard hold
a host-wide `flock` while they run, so only one agent injects at a time. A
second one waits briefly and then gets exit 42 naming the holder; reading —
`observe`, `shot`, `pixel`, `magnify`, `wait`, `status`, `doctor` — is never
serialized. Without this, two agents warping the same pointer produce a click
on the wrong window at exit 0.

## The eww indicator

`eww/cu.yuck` and `eww/cu.scss` are a ready widget: one mark per driving agent,
a tooltip carrying that agent's own `--why` and how long it has been running,
hidden entirely while nothing is happening, and replaced by a stop mark once
the user's abort flag is set. It listens to `cu status --follow --json`, whose
worst-case staleness is its 250 ms derived-state poll interval. Clicking a mark
sets that flag.

They name no path, colour, or monitor, so they are included by relative path
and styled by the configuration that includes them:

```yuck
(include "../../code/ai-agent-computer-use/eww/cu.yuck")
...
(cu-indicator)
```

```scss
@import "../../code/ai-agent-computer-use/eww/cu.scss";
.cu__driver  { color: $yellow; }
.cu__aborted { color: $red; }
```

## Telling an agent it is there

An agent reaches for the desktop only if it knows the desktop is reachable, so
one short section belongs in the context file of any project where that might
come up. [AGENTS.md.example](AGENTS.md.example) is that section, and it is
deliberately five lines: the fact that `cu` exists, what it is for, and that
bare `cu` teaches the rest. Anything more is a copy of the guide that goes stale
without anything failing.

## Status

`doctor`, `observe`, `shot`, `focus`, and the input commands — `move`, `click`,
`scroll`, `type`, `key` — are implemented, with the safety interlocks, as is
`wait`, `browser`, `recipe`, `know`, `sandbox`, and the `status`/`session`
feed a status bar reads, with an eww widget for it. Concurrent agents are
serialized on the desktop and reported individually. Every command in the
design is built — see
[docs/design.md](docs/design.md) for the command surface and build order, and
[docs/environment.md](docs/environment.md) for the verified host facts the
design rests on.

## Development

The two gates. Both tools and the rules they enforce are pinned in
`pyproject.toml`, so they report the same thing on any host.

```sh
uv run pytest
uv run ruff check .
```

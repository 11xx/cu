# ai-agent-computer-use

Computer use for AI coding harnesses on Linux/Wayland, targeting Hyprland.

Gives a multimodal agent the ability to see the screen and drive the pointer,
keyboard, and windows of a real desktop session, through a deterministic CLI
that any harness can call as a shell command. Claude Code and Codex are the
initial consumers; nothing in the interface is specific to either.

## Orientation

```sh
bin/cu                               # the workflow guide; no arguments needed
```

A bare `bin/cu` prints what a session needs before its first command: observe
before capturing, act through the frame you read, what each exit code obliges,
how hard the driving model should think, and which interlocks exist. `--help`
is the per-command reference and points back at it. There is no skill or prompt
fragment to install alongside — the CLI is the only workflow surface, so a
caller that reaches straight for `bin/cu click` can still find out what it owes.

## Usage

```sh
bin/cu doctor                        # verify every environment precondition
bin/cu observe                       # monitors, windows, focus, cursor
bin/cu shot                          # capture the focused window
bin/cu shot --output DP-2            # a whole monitor
bin/cu shot --region '100,100 400x200'
bin/cu shot --if-changed             # reuse the last frame if nothing moved
bin/cu shot --for openai             # capture at another consumer's budget
bin/cu profiles                      # what a capture costs each consumer

bin/cu wait window-open --class zen  # block on a compositor event
bin/cu focus zen                     # give a window keyboard focus
bin/cu move  --frame f3 --at 412,300 # warp the pointer to a frame coordinate
bin/cu click --frame f3 --at 412,300 # warp, check the target, press and release
bin/cu scroll --dy -3                # wheel where the pointer already is
bin/cu type 'hello'                  # clipboard by default, --method keys to synthesize
bin/cu type 'hello' --verify         # read the field back and confirm it arrived
bin/cu key --action paste            # a semantic action, resolved per application
bin/cu browser list                  # installed browsers and what each resolves
bin/cu browser focus-urlbar          # a semantic browser action, never a chord
bin/cu recipe save open-url --last 4 # promote a successful run from the log
bin/cu recipe replay open-url --param text1=example.com

bin/cu know set zen urlbar --rect 0.1,0.02,0.6,0.04   # remember where to look
bin/cu shot --landmark zen/urlbar    # aim a capture, widened as the window drifts
bin/cu sandbox create                # a headless output, when you want one

bin/cu session begin 'booking a flight'   # tell the user what is happening
bin/cu click --frame f3 --at 412,300 --why 'accepting the fare'
bin/cu status                        # one line for a status bar; empty when idle
bin/cu session end
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

`cu` publishes what it is doing to `$XDG_RUNTIME_DIR/cu/status.json` — one
small object, replaced atomically, so a status bar can poll it and show the
user that an agent is driving. It does not draw an overlay: `grim` captures
layer surfaces, so anything `cu` drew would end up in the frames the model
reads. Records expire, because nothing writes "finished" for a killed process.

## Status

`doctor`, `observe`, `shot`, `focus`, and the input commands — `move`, `click`,
`scroll`, `type`, `key` — are implemented, with the safety interlocks, as is
`wait`, `browser`, `recipe`, `know`, `sandbox`, and the `status`/`session`
feed a status bar reads. Every command in the design is built — see
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

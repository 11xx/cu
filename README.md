# ai-agent-computer-use

Computer use for AI coding harnesses on Linux/Wayland, targeting Hyprland.

Gives a multimodal agent the ability to see the screen and drive the pointer,
keyboard, and windows of a real desktop session, through a deterministic CLI
that any harness can call as a shell command. Claude Code and Codex are the
initial consumers; nothing in the interface is specific to either.

## Usage

```sh
bin/cu doctor                        # verify every environment precondition
bin/cu observe                       # monitors, windows, focus, cursor
bin/cu shot                          # capture the focused window
bin/cu shot --output DP-2            # a whole monitor
bin/cu shot --region '100,100 400x200'
bin/cu shot --if-changed             # reuse the last frame if nothing moved
```

All accept `--json`. `bin/cu` is a plain command — it resolves `uv` itself, so
a caller needs no knowledge of the Python underneath.

`shot` writes a PNG plus a **frame descriptor** recording the logical rectangle
the image depicts and its pixel size. Later commands take coordinates as
`--frame <id> --at x,y` in image pixels, and `cu` maps them back to the screen.

## Status

`doctor`, `observe`, and `shot` are implemented. Input, browser control, and
recipes are designed but not built — see [docs/design.md](docs/design.md) for
the command surface and build order, and
[docs/environment.md](docs/environment.md) for the verified host facts the
design rests on.

## Development

```sh
uv run --with pytest pytest tests/
```

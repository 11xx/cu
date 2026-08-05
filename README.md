# ai-agent-computer-use

Computer use for AI coding harnesses on Linux/Wayland, targeting Hyprland.

Gives a multimodal agent the ability to see the screen and drive the pointer,
keyboard, and windows of a real desktop session, through a deterministic CLI
that any harness can call as a shell command. Claude Code and Codex are the
initial consumers; nothing in the interface is specific to either.

## Usage

```sh
bin/cu doctor     # verify every environment precondition
bin/cu observe    # monitors, windows, focus, cursor
```

Both accept `--json`. `bin/cu` is a plain command — it resolves `uv` itself, so
a caller needs no knowledge of the Python underneath.

## Status

`doctor` and `observe` are implemented. Capture, input, browser control, and
recipes are designed but not built — see [docs/design.md](docs/design.md) for
the command surface and build order, and
[docs/environment.md](docs/environment.md) for the verified host facts the
design rests on.

## Development

```sh
uv run --with pytest pytest tests/
```

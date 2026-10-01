# cu

A command-line tool that lets an AI agent see and drive a Linux desktop
running Hyprland.

A coding agent that can only read files and run commands stops at the edge of
the terminal. It cannot check that a page renders, click through a settings
dialog, or confirm that the app it just built opens. Screenshot-and-click
scripts look like a fix but fail quietly: an image is downscaled, a coordinate
read from it lands on the wrong pixel, and the click still exits 0.

`cu` gives the agent the screen, pointer, keyboard, and windows through plain
shell commands that any harness can call. A capture writes a PNG plus a frame
record, and later commands aim at a frame id and image coordinates. `cu` maps
those back to the screen, so the agent never computes a screen position. Every
refusal has its own exit code. Input refuses while the screen is locked or
the user's abort flag is set. A window capture refuses when another window
covers the target, rather than raising it.

It works on the visible desktop by default, so the user can watch and stop it.
Working on a headless output takes an explicit `cu sandbox`.

## Install

Requires Hyprland on Wayland, [uv](https://docs.astral.sh/uv/), `grim`, and
write access to `/dev/uinput`. `wl-copy`, `wl-paste`, and `slurp` are optional.

```sh
git clone https://github.com/11xx/cu
cd cu
bin/cu doctor        # checks every precondition and names the remedy for each failure
make install         # puts `cu` on PATH
```

`make install` is an editable `uv tool` install, so the `cu` on PATH runs this
checkout. Keep the checkout where it is. Without installing, `bin/cu` is the
same command.

## Driving the desktop

```sh
cu                                   # the workflow guide
cu observe                           # monitors, windows, focus, cursor
cu shot                              # capture the focused window; prints a frame id
cu click --frame f3 --at 412,300     # aim with coordinates read from that frame
cu type 'hello' --verify             # type, then read the field back
cu shot --after f3 --watch full      # recapture once the pixels change and settle
```

Every command takes `--json`, and every injecting command takes `--dry-run`,
which runs all the checks without sending an event.

## Several agents, one desktop

```sh
export CU_SESSION=$(cu session begin --why 'booking a flight')
cu click --frame f3 --at 412,300 --why 'accepting the fare'
cu status                            # one line per driving agent
cu session end
```

Commands that inject input hold a host-wide lock, so two agents never move the
pointer at the same time; observation is never locked. The `--why` text is
what the user sees. `cu abort --set` stops every agent until the user clears
it.

## Optional pieces

- [`browser-extension/`](browser-extension/README.md): a Firefox extension
  for reading pages and acting on the DOM of a tab a session explicitly grants
  with `cu web grant`.
- [`adapters/`](adapters/README.md): a Node module and a Pi tool that combine
  actions, status, and a capture in one call.
- `eww/cu.yuck` and `eww/cu.scss`: a status-bar widget with one mark per
  driving agent, its `--why` in the tooltip, and a click that sets the abort
  flag. The include paths are relative to your eww config directory:

  ```yuck
  (include "<path-to-cu>/eww/cu.yuck")
  (cu-indicator)
  ```

  ```scss
  @import "<path-to-cu>/eww/cu.scss";
  .cu__driver  { color: $yellow; }
  .cu__aborted { color: $red; }
  ```

- [`AGENTS.md.example`](AGENTS.md.example): a five-line section for a
  project's agent instructions, so its agents know the desktop is reachable.

## More

Run `cu` with no arguments for the workflow guide and `cu <command> --help` for
each command's reference. [docs/design.md](docs/design.md) covers the design
and [docs/environment.md](docs/environment.md) the compositor behaviour it
depends on.

`make check` runs the full local gate: Python and Node tests, Ruff, and the
package build.

## License

Public domain, under the [Unlicense](UNLICENSE).

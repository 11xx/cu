# Host baseline

The verified capabilities this project builds on. Each fact carries the probe
that establishes it, so any claim can be re-checked on a new machine. `cu
doctor` automates these checks; this document explains what they mean.

## Session

| Property | Value | Probe |
| --- | --- | --- |
| Compositor | Hyprland 0.56.1 | `hyprctl version` |
| Session type | Wayland (`wayland-1`), XWayland present on `:0` | `env \| grep -E 'WAYLAND\|DISPLAY'` |
| Outputs | 2, mixed scale (see below) | `hyprctl monitors -j` |

## Hyprland speaks Lua, not dispatcher strings

Hyprland 0.56 replaced the classic `hyprctl dispatch <name> <args>` string form
with a Lua API. The old syntax is a parse error, not a silent no-op:

```
$ hyprctl dispatch movecursor 173 627
error: [string "return hl.dispatch(movecursor 173 627)"]:1: ')' expected near '173'
```

The interfaces this project uses:

| Call | Semantics |
| --- | --- |
| `hl.dsp.cursor.move(x, y)` | Absolute warp, global logical coordinates, accepts floats |
| `hl.get_cursor_pos()` | Returns `{x, y}` as floats in global logical coordinates |
| `hl.get_windows()`, `hl.get_monitors()` | Structured introspection; return userdata, not plain tables |
| `hl.dsp.send_key_state`, `hl.dsp.send_shortcut` | Keybind-oriented key injection |

Reach them with `hyprctl repl '<lua>'` (or `hyprctl eval`). Enumerate any
namespace with `hyprctl repl 'for k,v in pairs(hl.dsp) do ... end'`.

`hl.dsp.cursor.move` is **absolute**. Calling it with the cursor's own current
position leaves the cursor where it is; a relative implementation would have
doubled the coordinate:

```
before=(3057.2,863.8) ok=true err=nil after=(3057.2,863.8)
```

The event stream lives at
`$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock`. It is a
dotfile, so a bare `ls` of that directory does not show it.

## Input injection needs no root

`/dev/uinput` is `crw-rw----+ root input`, and the desktop user is in `input`,
so a plain uid-1000 process opens it for writing:

```
$ python3 -c 'import os; os.close(os.open("/dev/uinput", os.O_WRONLY))' && echo ok
ok
```

This removes the usual `ydotoold`-style root daemon from the design. A virtual
keyboard and pointer are created directly by the unprivileged process that
needs them, and disappear when it exits.

The permission comes from an ACL on the device node. It is a precondition, not
an invariant: `cu doctor` re-checks it rather than assuming it survives a
reboot or a udev change.

### xremap does not intercept virtual devices

A key remapper runs in this session, bound to one named physical device:

```
xremap /home/lobo/.config/xremap/browser-emacs.yaml --device SINO WEALTH USB KEYBOARD
```

Because the binding is device-scoped, keys injected through a new virtual
keyboard reach applications unremapped. Were the `--device` filter ever
dropped, xremap would grab all keyboards including the virtual one, and every
injected keystroke would be silently rewritten. `cu doctor` checks for a
device-scoped filter and warns when it cannot find one.

## Coordinates: three spaces, two scales

```
DP-2   2560x1440 physical   scale 1.25   logical 2048x1152 at (0,0)
DP-1   2560x1440 physical   scale 1.00   logical 2560x1440 at (2048,0)
```

The logical desktop is 4608x1440. Hyprland reports window geometry, cursor
position, and accepts cursor moves in this logical space — `hyprctl
activewindow` on DP-2 returns `at: [3,29] size: [2042,1120]`, which fits inside
2048x1152 and would overflow the physical 2560x1440.

Capture works in physical pixels, and the two forms differ sharply:

| Command | Output | Notes |
| --- | --- | --- |
| `grim -o DP-1` | 2560x1440 | Native pixels of that output |
| `grim -o DP-2` | 2560x1440 | Native pixels; 2048x1152 logical |
| `grim` (whole desktop) | 5760x1800 | Logical bounding box times the **largest** scale |

Full-desktop capture composites at scale 1.25, so DP-1's native 2560x1440 is
upscaled to 3200x1800 — 10.4 megapixels that carry less detail than the two
native captures totalling 7.4. Per-output capture is therefore the primitive,
and whole-desktop capture is never used.

### Region capture speaks logical coordinates

`grim -g "<x>,<y> <w>x<h>"` takes the same logical coordinates Hyprland reports
and accepts, which makes a window's reported geometry directly usable as a
capture argument. The resulting *pixel* dimensions are the logical size times
the scale of the output the region lands on:

| Region | Lands on | Output |
| --- | --- | --- |
| `0,0 100x100` | DP-2, scale 1.25 | 125x125 |
| `2100,100 100x100` | DP-1, scale 1.00 | 100x100 |
| `2000,100 200x100` | spans both | 250x125 |

The spanning case follows the whole-desktop rule: it composites at the largest
scale of the outputs it touches, so the DP-1 portion is upscaled. A region is
therefore always clamped to a single output.

Because pixel dimensions vary by output, they are read from the captured file
rather than computed — which is what keeps the frame transform correct without
the caller tracking scales.

## Available tooling

Present: `hyprctl`, `grim`, `slurp`, `wl-copy`/`wl-paste`, `hyprpicker`, `wev`,
`magick`, `jq`, `socat`, `python3`, `uv`, `node`, `wf-recorder`, `pipewire`,
`xdg-desktop-portal-hyprland`.

Absent: `wtype`, `ydotool`, `dotool`, `xdotool` — no input-injection tool is
installed, which the direct `/dev/uinput` path makes unnecessary.

## Browsers

Six, across both engine families:

| Browser | Family | Executable | Window class |
| --- | --- | --- | --- |
| Zen | Gecko | `/opt/zen-browser-bin/zen-bin` (wrapper on `PATH` as `zen-browser`) | `zen` |
| Firefox | Gecko | wrapper on `PATH` | — |
| Floorp | Gecko | `/usr/bin/floorp` | — |
| Thorium | Chromium | `/usr/bin/thorium-browser` | — |
| Helium | Chromium | `helium-browser` → `/opt/helium-browser-bin/helium-wrapper` | `helium` |
| Vivaldi | Chromium | `/usr/bin/vivaldi` | — |

Window classes are recorded as they are observed; `zen` and `helium` are
confirmed from `hyprctl clients -j` with both running as native Wayland clients
(`xwayland: false`).

**Binary names do not follow the product name.** Helium is on `PATH` as
`helium-browser`, not `helium`; Thorium likewise. Probing a guessed name and
reading absence as "not installed" is how a browser goes missing from an
inventory. Discovery reads the `Exec=` line of desktop entries, which is
authoritative, and treats `PATH` name-matching as a hint only.

Both running browsers were launched with no remote-debugging or automation
flags, which is the normal case and the one the design has to accommodate.

No `playwright`, `geckodriver`, or `chromedriver` is installed. Chromium's
bundled `chromedriver` ships inside `/opt/helium-browser-bin/`.

## The keyboard is a three-stage pipeline, not a constant

This desktop rebinds keys twice before an application sees them, so no default
shortcut can be assumed.

**Stage 1 — xremap.** `~/.config/xremap/browser-emacs.yaml` applies an
Emacs-style editing layer, scoped by window class to browsers and two other
applications:

```yaml
application:
  only: [firefox, vivaldi-stable, thorium-browser, zen, helium, ...]
```

Floorp is absent from that list and so receives no layer. Within it, familiar
chords mean something else entirely: `C-f` becomes `right`, `C-a` becomes
`home`, `C-s` becomes `f3` (find-again), `C-w` becomes cut. `C-x` and `C-c` are
prefix keys opening submaps.

**Stage 2 — the browser's own bindings.** The Emacs layer deliberately targets
unusual chords that the browser is then configured to recognize.
`~/.zen/customKeys.json` completes the chain:

| Action | Zen binding | Reached from |
| --- | --- | --- |
| Focus URL bar | `Ctrl+Alt+Shift+F4` | — |
| Close tab | `Ctrl+Alt+Shift+Delete` | `C-M-k` |
| Save page as | `Ctrl+Alt+Shift+Insert` | `C-x C-w` |
| Close window | `Ctrl+Alt+Shift+End` | `C-x 4 0` |
| Back / forward | `Ctrl+Alt+Shift+B` / `+F` | `C-x M-b` / `C-x M-f` |
| **New tab** | `Ctrl+T` | — |

The last row is the point: **some bindings are customized and some are not**,
and the difference is not predictable. Of the four chords one would reflexively
call universal, `Ctrl+T` survives, `Ctrl+L` and `Ctrl+W` do not, and `Ctrl+F`
is consumed upstream by stage 1. Firefox carries its own `customKeys.json`;
Vivaldi's `Preferences` has no `keyboard` key, so it is unmodified.

**Stage 3 — the application acts.**

### What this means for injected input

Injected keys enter the pipeline *after* xremap, because xremap is bound to one
physical device. So the correct thing to inject is the **stage-2 chord the
browser actually listens for** — `Ctrl+Alt+Shift+F4` to focus Zen's URL bar —
never the physical chord a human would press, and never a documented default.

### The xremap fallback is a live hazard

`~/.local/bin/xremap-default` degrades to running xremap with **no device
filter** when the keyboard is absent:

```sh
notify-send ... "Device not found, running without --device filter"
xremap ~/.config/xremap/browser-emacs.yaml
```

In that state xremap grabs every keyboard including a virtual one, and injected
keys are silently remapped — `C-f` would arrive as `right`. The device filter
is therefore a property of the *running process*, not of the config, so
`cu doctor` reads `/proc/<pid>/cmdline` of the live xremap rather than trusting
the launcher's intent.

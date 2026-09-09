# How this compositor behaves

Why `cu` is built the way it is: the interfaces it drives, the ways they fail,
and the probe that settles each claim. Every fact here is about Hyprland and
Wayland rather than about one machine, so it holds anywhere the tool runs.

**Readings are not in this document, deliberately.** Which compositor version
is running, how many outputs there are and at what scales, which tools are
installed — these change per machine and go stale silently in a file. `cu
doctor` measures them at runtime and is the answer to all of them. What a run
*learns* about a particular host, and cannot probe for, belongs in
`cu know host`.

The distinction is worth keeping sharp while editing here. A sentence naming a
version, an output, a path under someone's home directory, or a specific
keybinding is a reading, and it belongs in one of those two places instead.

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
| `hl.dsp.*` | **Constructs** a dispatcher object; does not perform anything |
| `hl.dispatch(<dispatcher>)` | Executes one |
| `hl.get_cursor_pos()` | Returns `{x, y}` as floats in global logical coordinates |
| `hl.get_windows()`, `hl.get_monitors()` | Structured introspection; return userdata, not plain tables |
| `hl.dsp.send_key_state`, `hl.dsp.send_shortcut` | Keybind-oriented key injection |

Reach them with `hyprctl repl '<lua>'` (or `hyprctl eval`). Enumerate any
namespace with `hyprctl repl 'for k,v in pairs(hl.dsp) do ... end'`.

### Cursor movement, and how easily it looks like it works

The working form takes a **table**, and must be dispatched:

```lua
hl.dispatch(hl.dsp.cursor.move({x = 900, y = 500}))
```

Every plausible variant fails *silently* — `pcall` reports success, no error is
raised, and the cursor does not move:

| Call | Result |
| --- | --- |
| `hl.dsp.cursor.move({x=900,y=500})` without `hl.dispatch` | constructs and discards a dispatcher |
| `hl.dispatch(hl.dsp.cursor.move(900, 500))` | positional numbers ignored |
| `hl.dispatch(hl.dsp.cursor.move("900 500"))` | old string form ignored |
| `hl.dispatch(hl.dsp.cursor.move("exact 900 500"))` | ignored |

So a probe must move the cursor to a coordinate it is **not already at**.
Verifying with the cursor's own current position cannot distinguish a working
call from a no-op, and will report success for all five forms above.

Measured with the correct form: movement is absolute (`move(700,400)` twice is
idempotent), crosses output boundaries into the neighbouring monitor's logical
range, and preserves fractional coordinates exactly — `move(1234.5, 678.25)`
reads back as `(1234.50, 678.25)`.

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

A key remapper such as xremap is typically launched bound to one named physical
device:

```
xremap <config> --device '<physical keyboard>'
```

Because the binding is device-scoped, keys injected through a *new* virtual
keyboard are not that device and reach applications unremapped — which is what
makes direct injection viable at all. Were the `--device` filter ever dropped,
the remapper would grab every keyboard including the virtual one, and each
injected keystroke would be silently rewritten. `cu doctor` checks the running
process for a device-scoped filter and warns when it cannot find one.

## Coordinates: three spaces, two scales

Worked through a two-output example, since one scale hides every mistake this
section is about. `cu doctor` and `cu observe` report what any given machine
actually has.

```
OUT-A   2560x1440 physical   scale 1.25   logical 2048x1152 at (0,0)
OUT-B   2560x1440 physical   scale 1.00   logical 2560x1440 at (2048,0)
```

The logical desktop is then 4608x1440. Hyprland reports window geometry, cursor
position, and accepts cursor moves in this logical space — a maximized window on
OUT-A reports a size near 2048x1152, which would overflow its physical
2560x1440. Logical and physical are different numbers for the same window, and
nothing in either says which one it is.

Capture works in physical pixels, and the two forms differ sharply:

| Command | Output | Notes |
| --- | --- | --- |
| `grim -o OUT-B` | 2560x1440 | Native pixels of that output |
| `grim -o OUT-A` | 2560x1440 | Native pixels; 2048x1152 logical |
| `grim` (whole desktop) | 5760x1800 | Logical bounding box times the **largest** scale |

Full-desktop capture composites at the largest scale in play, so OUT-B's native
2560x1440 is upscaled to 3200x1800 — 10.4 megapixels carrying less detail than
the two native captures totalling 7.4. Per-output capture is therefore the primitive,
and whole-desktop capture is never used.

### Region capture speaks logical coordinates

`grim -g "<x>,<y> <w>x<h>"` takes the same logical coordinates Hyprland reports
and accepts, which makes a window's reported geometry directly usable as a
capture argument. The resulting *pixel* dimensions are the logical size times
the scale of the output the region lands on:

| Region | Lands on | Output |
| --- | --- | --- |
| `0,0 100x100` | OUT-A, scale 1.25 | 125x125 |
| `2100,100 100x100` | OUT-B, scale 1.00 | 100x100 |
| `2000,100 200x100` | spans both | 250x125 |

The spanning case follows the whole-desktop rule: it composites at the largest
scale of the outputs it touches, so the OUT-B portion is upscaled. A region is
therefore always clamped to a single output.

Because pixel dimensions vary by output, they are read from the captured file
rather than computed — which is what keeps the frame transform correct without
the caller tracking scales.

## External tooling

`cu` shells out for three things: `hyprctl` for compositor state and dispatch,
`grim` for capture, and `wl-copy`/`wl-paste` for the clipboard path that
`cu type` uses by default. Each is checked by `cu doctor`, which reports what is
actually present rather than what a document once recorded.

It deliberately does **not** depend on an input-injection tool — `wtype`,
`ydotool`, `dotool`, `xdotool`. Writing to `/dev/uinput` directly produces
genuine libinput events, which is what makes injected input indistinguishable
from a human's and is why no such dependency is wanted.

## Headless outputs work, and are the safe test target

`hyprctl output create headless` adds a real output the compositor treats like
any other, and `hyprctl output remove <name>` takes it away. Measured end to
end:

- It appears as a named `HEADLESS-<n>` output with its own resolution, scale,
  and workspace, placed beyond the real outputs.
- Existing outputs keep their geometry, scale, and active workspace exactly.
- A window directed to its workspace lands on it and reports logical geometry
  inside its logical bounds.
- `grim -o <name>` captures real rendered content from it, and a region crop
  obeys the same scale rule as any other output.
- After removal, `hyprctl monitors` and `hyprctl workspaces` match their
  pre-probe snapshots byte for byte.

So the input and capture paths can be developed against a screen that does not
physically exist, on the live compositor, without touching the user's displays.
A headless output also tends to carry a scale the physical ones do not, which
makes it worth testing against: coordinate math that is wrong for a third
distinct scale stays hidden while only two are in play.

## Sleeping monitors: what still works, and what wakes them

`hypridle` powers the displays down after 5.5 minutes idle and restores them on
activity. Whether an agent can work against a dark desktop depends on which of
its actions count as activity, and the answer splits cleanly. Measured by
disabling DPMS, performing one action, and re-reading `dpmsStatus`:

| Action | Wakes the monitors | Works while asleep |
| --- | --- | --- |
| `hyprctl` queries — `observe` | no | yes |
| Cursor warp — `hl.dsp.cursor.move` | no | yes |
| Keys via `hl.dsp.send_key_state` | **no** | yes |
| `grim -o` on a **physical** output | no | **no — fails** |
| `grim -o` on a **headless** output | no | yes |
| Any `/dev/uinput` event | **yes** | yes |

The dividing line is libinput. A uinput device produces genuine kernel input
events, which is exactly what makes them indistinguishable from a human — and
is also why they reset the session idle timer and turn every panel back on. The
compositor's own dispatchers never enter that path, so they leave a sleeping
desktop asleep.

Capture is the other half. A powered-down physical output may not produce the
frames a screenshot client awaits. Depending on the capture path, `grim` can
fail immediately or remain blocked until CU's 10-second subprocess timeout.
Successful compositor queries and `cu doctor` do not prove that an output is
rendering. Check the compositor's `dpmsStatus` when capture stalls. Waking a
physical output is an explicit desktop state change, not an automatic capture
fallback. A headless output has no physical panel and can provide a rendering
target while physical displays sleep; create one deliberately with `cu sandbox`.

So a **keyboard-driven agent can run entirely against sleeping displays**:
observe, warp, capture from a headless output, and inject keys through
`--backend compositor`. What cannot join it is the pointer. The compositor
exposes no button or wheel dispatcher, so clicks and scrolls must use uinput
and will always wake the screen.

### The dispatcher's argument shapes, both of which fail silently

```lua
hl.dispatch(hl.dsp.send_key_state({mods = "CTRL SHIFT", key = "v", state = "down"}))
```

- **`mods` is a name, not a bitmask.** `mods = 4` is accepted and produces an
  *unmodified* keystroke. Several names separate with a space, `+`, or `,` —
  all three were verified to work.
- **`key` is an X11 keysym name** (`"a"`, `"Return"`, `"F4"`) **or an xkb
  keycode**, which is the evdev code plus 8 — so `key = 30` types `u`, not `a`.
  A kernel constant such as `"KEY_A"` is accepted and does nothing.
- **`state` must be `"down"`, `"up"`, or `"repeat"`.** Unlike the other two, a
  wrong value here is a real error rather than a silent no-op.

## Browsers

Browsers fall into two engine families, Gecko and Chromium, and the family
decides where the keymap lives. Which are installed on any given machine is a
reading: `cu browser list` enumerates them, with what each resolves.

**Binary names do not follow the product name.** A browser is routinely on
`PATH` under a name its users would not guess — a `-browser` suffix, a wrapper
around a differently-named binary. Probing a guessed name and reading absence
as "not installed" is how a browser goes missing from an inventory, so
discovery reads the `Exec=` line of desktop entries, which is authoritative,
and treats `PATH` name-matching as a hint only.

**A window class is not the product name either**, and is recorded as observed
from `hyprctl clients -j` rather than assumed. A desktop entry may declare one;
several do not, which is why the executable name is the fallback.

Browsers are assumed to be launched with no remote-debugging or automation
flags. That is the normal case, and the one the design has to accommodate.

A WebDriver stack — `playwright`, `geckodriver`, `chromedriver` — is not
assumed to be present, and `cu` does not require one. Chromium builds often
ship a bundled `chromedriver` beside the browser, which is worth knowing exists
but is not a dependency: driving through one would mean a separate browser
session rather than the user's own.

## The keyboard is a three-stage pipeline, not a constant

A keystroke can be rewritten more than once before an application acts on it,
so **no default shortcut can be assumed** — which is the reason `cu key`
refuses an unmapped action rather than falling back to a documented default.

**Stage 1 — a device-bound remapper.** A tool such as xremap grabs a physical
keyboard and rewrites chords before anything else sees them, typically scoped
by window class so the layer applies to some applications and not others.
Within its scope, familiar chords can mean something entirely different, and
some become prefix keys that open a submap.

**Stage 2 — the application's own bindings.** A remapping layer usually targets
deliberately unusual chords that the application is then configured to
recognize, which is what completes the chain.

**Stage 3 — the application acts.**

The consequence is the part that generalizes: **some bindings are customized
and some are not, and which is which is not predictable from the chord.** Two
chords that look equally universal can differ — one surviving untouched, the
other consumed at stage 1 and never reaching the application at all. Nothing
about a chord reveals which case it is, so the only sound answer is to read the
configuration rather than to reason about the shortcut.

Which chords a particular host actually rewrites is a reading, not a fact about
the compositor. `cu browser` reads each browser's own keymap where it can, the
`[keys]` table in the user's config states the rest, and anything learned the
hard way belongs in `cu know host`.

### What this means for injected input

Injected keys enter the pipeline *after* stage 1, because the remapper is bound
to one physical device and the injected device is not it. So the correct thing
to inject is the **stage-2 chord the application actually listens for**, never
the physical chord a human would press, and never a documented default.

### An unfiltered remapper is a live hazard

A device-bound remapper is only device-bound while its filter is actually in
effect. A launcher that degrades to running without one — when the keyboard it
names is absent, say — leaves the remapper grabbing *every* keyboard, including
the virtual one `cu` creates. Injected keys are then silently rewritten on
their way out, and the failure is invisible: the keystroke arrives, and does
something else.

The filter is therefore a property of the **running process**, not of the
config file, and the two disagree exactly when it matters. `cu doctor` reads
the live process's `/proc/<pid>/cmdline` rather than trusting a launcher's
intent, because a config that specifies a filter proves nothing about the
process currently holding the keyboard.

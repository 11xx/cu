# Computer use

This host has `cu`, a CLI that sees and drives the desktop. Use it when a task
needs the real screen rather than the filesystem.

```sh
cu observe --json                  # monitors, windows, focus, cursor — cheap and exact
cu shot --window active            # capture; prints a frame id and a PNG path
cu click --frame f3 --at 412,300   # coordinates are image pixels of that frame
cu type 'text'                     # clipboard by default
cu key --action paste              # semantic, resolved per application
cu browser focus-urlbar            # semantic, resolved per browser
cu wait window-open --class zen    # block on an event instead of polling
```

Observe before capturing: the compositor already knows which window is focused
and where it sits, so a screenshot is for reading content, not for locating
windows.

Never compute a screen coordinate. Report image coordinates against the frame
they came from; `cu` owns every transform between image pixels, logical
coordinates, and physical pixels.

Exit codes carry the contract, and the right response differs per code. `cu
doctor --json` prints the full table. The one that matters most:

- **40 — the user aborted.** Stop. Do not retry, and do not attempt to clear
  the flag. It is the only interlock they hold.
- 20 stale frame → re-capture. 21 focus or target changed → re-observe.
  41 the user is moving the mouse → yield. 50 no known binding → say so rather
  than guessing a chord.

Chords are not portable on this desktop: keys pass through a remapping layer
and then each application's bindings. Prefer `--action`. A code 50 means the
binding is genuinely unknown, and guessing a default would silently perform a
different command.

`cu know` carries what earlier runs learned about an application, and
`cu shot --landmark app/name` aims a capture from it. A landmark says where to
look, never where to click: read the pixels and act through the frame. Record
landmarks and notes as you discover them, and `cu know confirm`/`miss` after
using one.

`cu` aims at the visible desktop, which is what lets the user watch and stop
you. `cu sandbox create` is the explicit way to work on a headless output; say
so when you do, and remove it afterwards.

Every injecting command takes `--dry-run`. Recipes do not replay until a human
has read and approved them; never approve on the user's behalf.

---
name: computer-use
description: See and drive this Linux/Wayland desktop — pointer, keyboard, windows, browsers — through the `cu` CLI. Use when a task needs the real screen rather than the filesystem: reading what an application is showing, clicking or typing into a GUI, driving a browser with the user's own profile, or checking that something actually appeared on screen. Not for tasks that a shell command or file read answers directly.
---

# Computer use

`cu` is a plain command. Run it, read its JSON, act on its exit code. There is
no state to hold between calls except frame ids, and those live on disk.

## The loop

**Observe before you look.** `cu observe --json` returns every monitor, window,
title, geometry, and the focused window for a few hundred tokens. It answers
most of what a screenshot gets asked — which window is focused, whether the
dialog opened, where the terminal is — exactly and cheaply. A screenshot is for
reading *content*, not for locating windows.

**Capture only what you need to read.**

```sh
cu shot --window active           # the usual first call
cu shot --output DP-2             # a whole monitor
cu shot --region '100,100 400x200'
cu shot --if-changed              # reuse the last frame if nothing moved
```

Every capture prints a **frame id** and a path. Read the PNG at that path.

**Act through the frame you read.** Coordinates are always image pixels from a
specific frame, and always paired with its id:

```sh
cu click --frame f3 --at 412,300
```

Never compute a screen coordinate yourself. `cu` owns the transform between
image pixels, the compositor's logical space, and physical pixels — that
arithmetic is the single largest source of confidently-wrong clicks, and it is
already done for you.

**Wait on events, don't poll.**

```sh
cu wait window-open --class zen --timeout 20
```

## Exit codes are the contract

Read them; they say what to do next, and the response differs per code.

| Code | Meaning | Do |
| --- | --- | --- |
| 0 | Fine | Continue |
| 2 | Malformed command | Fix the arguments |
| 10 | Environment unusable | Run `cu doctor`; retrying will not help |
| 20 | Frame no longer describes the screen | Capture a new one, retry |
| 21 | Focus or target changed | Re-observe, then decide again |
| 22 | Window, output, or browser is gone | Re-observe |
| 23 | Region spans outputs or is off-screen | Widen to `--window` or `--output` |
| 24 | Awaited event never arrived | Re-observe; it may have happened first |
| 30 | Denylist, locked session, or unreviewed recipe | Do not retry; tell the user |
| **40** | **The user aborted** | **Stop. Do not retry. Do not try to clear it.** |
| 41 | The user is moving the mouse | Yield; wait before retrying |
| 50 | No known binding for that action | Do not guess a chord; say so |

`cu doctor --json` ships this table, so it never has to be remembered.

**Code 40 is not a transient failure.** It means the person at the desk pressed
their abort key. Stop what you are doing and tell them. Attempting to clear the
flag or retry past it defeats the only interlock they hold.

## Typing and keys

```sh
cu type 'some text'            # clipboard by default; restores what was there
cu type 'x' --method keys      # per-character, for apps that reject paste
cu key --action paste          # semantic, resolved for the focused application
cu key --chord ctrl+alt+shift+f4
```

**Do not assume a chord means what it does elsewhere.** On this desktop keys
pass through a remapping layer and then each application's own bindings.
`Ctrl+T` survives; `Ctrl+L` and `Ctrl+W` do not. Prefer `--action`, and if `cu`
answers code 50, the binding is genuinely unknown — say so rather than guessing
a default, which would silently perform some other command.

## Browsers

Drive chrome by action, page content by coordinates.

```sh
cu browser list                # what is installed and what each resolves
cu browser actions             # what the focused browser can be driven by
cu browser focus-urlbar
cu browser close-tab
```

Actions resolve from each browser's own configuration. A Chromium-family
browser resolves nothing until the user configures it — that is honest, not
broken.

## Reuse what earlier runs learned

```sh
cu know show zen                   # landmarks and notes for an application
cu shot --landmark zen/urlbar      # aim a capture by a remembered position
cu know set zen urlbar --rect 0.1,0.02,0.6,0.04 --note 'the address field'
cu know note zen 'the cookie banner must be dismissed first'
cu know confirm zen urlbar         # or `miss`, when it pointed at the wrong thing
```

A landmark says **where to look, never where to click.** It aims a capture,
derived from the window's geometry now; read those pixels and act through the
frame, exactly as you would without it. Never turn a remembered position into a
click coordinate — a control that has moved would be clicked confidently and
the click would land on something.

Record what you learn as you go, and score a landmark after using it. That is
what makes the next run cheaper.

## Working where the user cannot see

`cu` aims at the visible desktop, and that is a safety property: the user can
watch and stop you. `cu sandbox create` makes a headless output when you
genuinely need one — for testing input without touching their screen — and it
should be a deliberate choice you mention, not a default. Remove it when done.

## Safety, and what to tell the user

- Every injecting command takes `--dry-run`, which runs every check and logs
  the action without emitting an event. Use it when unsure.
- `cu` refuses to type into password managers, lock screens, and windows whose
  title looks like a credential prompt. If it refuses, do not work around it.
- Recipes will not replay until a human has read and approved them. Show the
  steps and ask; never approve on the user's behalf.

## When a capture surprises you, widen

A narrow crop aimed at the wrong place returns a perfectly sharp image of
something irrelevant, and nothing in the picture says so. Wide captures fail
loudly; narrow ones fail quietly. On the first surprise go to `--window`, then
`--output`, rather than re-aiming repeatedly. Two aimed captures that disagree
with expectation cost more than one full-output capture.

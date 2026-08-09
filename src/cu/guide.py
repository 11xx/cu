"""The bare-`cu` guide: what a session needs before its first command.

`--help` is the reference — every command, every flag. This is the
orientation: what `cu` owns, the order the commands are meant to be used in,
and the judgment that decides what to do with what comes back. Anything
belonging to one command's contract stays in that command's `--help`.

Every claim here must be traceable to code. A guide that promises more than
the tool does is worse than no guide, because it is believed.
"""

from __future__ import annotations

GUIDE = """cu — see and drive this Linux/Wayland desktop: pointer, keyboard, windows, browsers.

Use it when a task needs the real screen rather than the filesystem. `cu` acts
on the **visible desktop** by default, and that is a safety property rather
than a limitation: the user can watch what you do and stop you.

There is no state to hold between calls except frame ids, and those live on
disk. Every command takes --json.

OBSERVE BEFORE YOU LOOK
  cu observe --json              Monitors, windows, titles, geometry, focus, cursor.

  The compositor already knows which window is focused and where it sits, so
  this answers most of what a screenshot gets asked — which window is focused,
  whether the dialog opened, where the terminal is — exactly, and for a few
  hundred tokens. A screenshot is for reading *content*, not for locating
  windows.

CAPTURE ONLY WHAT YOU NEED TO READ
  cu shot --window active        The usual first call.
  cu shot --output DP-2          A whole monitor.
  cu shot --region '100,100 400x200'
  cu shot --if-changed           Reuse the last frame when the pixels match.

  Every capture prints a frame id and a path. Read the PNG at that path, along
  with what the image will cost whoever reads it.

  cu profiles                    What a capture costs each configured consumer.

  Image size is one of the two things you control that changes cost and
  latency, and consumers do not price it alike — they bill in patches, and the
  patch is not the same size everywhere. --for names the consumer, and a new
  one is a stanza under [capture.profiles] rather than a change to cu.

ACT THROUGH THE FRAME YOU READ
  cu click --frame f3 --at 412,300

  Coordinates are image pixels of a specific frame, always paired with its id.
  Never compute a screen coordinate yourself. `cu` owns every transform between
  image pixels, the compositor's logical space, and physical pixels — that
  arithmetic is the largest single source of confidently-wrong clicks, and it
  is already done for you.

  Every injecting command takes --dry-run, which runs each interlock and writes
  the same log entry without emitting an event.

WAIT ON EVENTS, DO NOT POLL
  cu wait window-open --class zen --timeout 20

EXIT CODES ARE THE CONTRACT
  Read them. They say what to do next, and the right response differs per code.
  `cu doctor --json` carries the full table, so it never has to be remembered.

   0  Fine                              Continue
   2  Malformed command                 Fix the arguments
  10  Environment unusable              Run `cu doctor`; retrying will not help
  20  Frame no longer describes screen  Capture a new one, retry
  21  Focus or target changed           Re-observe, then decide again
  22  Window, output, or browser gone   Re-observe
  23  Region spans outputs/off-screen   Widen to --window or --output
  24  Awaited event never arrived       Re-observe; it may have happened first
  25  Typed text did not arrive intact  Read the field; do NOT blindly retype
  30  Denylist, lock, unreviewed recipe Do not retry; tell the user
  40  The user aborted                  Stop. Do not retry. Do not clear it.
  41  The user is moving the mouse      Yield; wait before retrying
  42  Another agent is driving          Wait for it to finish, then retry
  43  An agent tried to change abort    Ask the user to set or clear it
  50  No known binding for that action  Do not guess a chord; say so

  Code 40 is not a transient failure. It means the person at the desk pressed
  their abort key. Stop, and tell them. Retrying past it or trying to clear the
  flag defeats the only interlock they hold and you cannot.

THE USER'S ABORT
  cu abort                            Report whether the abort is set.
  cu abort --set                      Set it and stop every agent at its next action.
  cu abort --clear                    Release it.

  The abort flag is host-wide and belongs to the user. An agent-marked caller
  (`CU_SESSION` or `--session`) gets exit 43 for either mutation and asks the
  user instead. This prevents normal agent paths from changing it by accident;
  it cannot stop a determined agent from bypassing the command.

HOW HARD TO THINK
  Reasoning effort and capture size are separate costs. Lower effort shortens
  the pause between receiving a frame and choosing the next action; it does not
  make the screenshot itself faster. Image size is the other knob, and --for
  sets it.

  Medium effort is the default. Low is for a known interface with one obvious
  target, a reversible action, evidence from `cu observe` rather than pixels,
  an approved recipe, and verification straight after. Higher effort is for
  genuinely hard visual diagnosis — never as a substitute for asking the user
  before submitting, deleting, paying, or touching credentials.

  Escalate out of low on a signal rather than a feeling: a frame that surprises
  you, a landmark that resolved `widened` or `stale`, a modal that appeared
  unasked, an action that failed for a reason you cannot name, several controls
  that plausibly match, or a next step that is hard to reverse.

  The cheapest turn is no model turn. An approved `cu recipe replay` keeps every
  runtime interlock without spending a visual decision at all.

SAY WHAT YOU ARE DOING
  export CU_SESSION=$(cu session begin --why 'booking a flight')
  cu click --frame f3 --at 412,300 --why 'accepting the fare'
  cu session end

  The user's status bar reads this, and it is how they decide whether to
  interrupt you or leave you alone. A session's --why is its standing purpose;
  each command's --why is one short phrase about that action's intent, not a
  restatement of the command: "accepting the fare", not "click at 412,300".
  Open a session for anything spanning more than a couple of commands —
  without one the indicator clears while you are thinking, which reads to the
  user as "it finished".

  `session begin` prints an id, and every command inside the session must say
  it belongs there: through CU_SESSION as above, or --session ID if you cannot
  set an environment variable between commands. It is never inferred, because
  you may not be the only agent on this desktop and guessing would file your
  actions under someone else's intent.

YOU MAY NOT BE ALONE HERE
  The desktop is one pointer, one keyboard focus, and one clipboard. `cu`
  serializes the commands that touch them, so only one agent injects at a time
  and the rest wait briefly. Past that wait you get exit 42 naming who holds
  it: that is another agent working, not a fault. Wait and retry.

  Reading is never serialized — observe, shot, wait, status, and doctor answer
  while someone else is driving, which is how you find out what is going on.
  `cu status` lists every agent currently driving, not just you.

TYPING AND KEYS
  cu type 'some text'            Clipboard by default; restores what was there.
  cu type 'some text' --verify   Read the field back and confirm it arrived.
  cu type 'x' --method keys      Per-character, for fields that reject a paste.
  cu key --action paste          Semantic, resolved for the focused application.
  cu key --chord ctrl+alt+shift+f4

  Use --verify for anything that matters. Without it, a paste an application
  silently rejected is indistinguishable from success. It answers `ok`, or
  `unverified` when the field could not be read back — which is not the same as
  success — or exits 25. Verifying leaves the field selected, so submit next,
  or re-aim before typing again: the next keystroke would replace what is there.

  Chords are not portable. A keystroke passes through whatever remapping this
  host runs and then the application's own bindings, so a chord that means one
  thing elsewhere may mean something else here. Prefer --action. Exit 50 means
  the binding is genuinely unknown — say so rather than guessing a default,
  which would silently perform some other command.

BROWSERS
  cu browser list                What is installed, and what each resolves.
  cu browser actions             What the focused browser can be driven by.
  cu browser focus-urlbar
  cu browser close-tab

  Drive chrome by action and page content by coordinates. Actions resolve from
  each browser's own configuration; a browser that resolves nothing until the
  user configures it is being honest, not broken.

WHAT EARLIER RUNS LEARNED
  cu know host                   What this machine does differently.
  cu know show zen               Landmarks and notes for an application.
  cu shot --landmark zen/urlbar  Aim a capture from a remembered position.
  cu know set zen urlbar --rect 0.1,0.02,0.6,0.04 --note 'the address field'
  cu know note zen 'dismiss the cookie banner before anything else responds'
  cu know confirm zen urlbar     Or `miss`, when it pointed at the wrong thing.

  A landmark says where to **look**, never where to click. It aims a capture,
  derived from the window's geometry now; read those pixels and act through the
  frame exactly as you would without it. A remembered position turned into a
  click coordinate is a control that has since moved, clicked confidently.

  It steers rather than asserts, and says how much to believe it. A window the
  size it was learned at resolves `exact`. A resized one resolves `widened`,
  covering every way the control could have followed the resize — so search
  what comes back rather than expecting the control centred. Too far, and it
  resolves `stale` and hands back the whole window, which is what you would
  have captured anyway.

  Record what you learn as you go, and score a landmark after using it. That is
  what makes the next run cheaper.

  Some of what you learn is not about any application — that keystrokes pass
  through a remapping layer here, that one output reports a scale the others do
  not. `cu know host '<fact>'` keeps those. What cu can measure for itself is
  `cu doctor`'s answer, not something to record.

WHEN A CAPTURE SURPRISES YOU, WIDEN
  A narrow crop aimed at the wrong place returns a perfectly sharp image of
  something irrelevant, and nothing in the picture says so. Wide captures fail
  loudly; narrow ones fail quietly. On the first surprise go to --window, then
  --output, rather than re-aiming repeatedly — two aimed captures that disagree
  with expectation cost more than one full-output capture.

THE INTERLOCKS, AND WHAT TO TELL THE USER
  `cu` refuses to type into password managers, lock screens, and windows whose
  title reads like a credential prompt. The list is a default the user's config
  can change. If it refuses, do not work around it.

  Recipes do not replay until a human has read and approved them. Show the
  steps and ask; never approve on the user's behalf. A pointer recipe also
  refuses when its window was materially resized — re-record and review it,
  because a stored fraction cannot reveal how a control followed a resize.

  `cu sandbox create` makes a headless output for working where nobody can see.
  That should be a deliberate choice you mention, not a default, and you should
  remove it when you are done.

  cu <command> --help for a command's full contract.
"""


def render() -> str:
    return GUIDE

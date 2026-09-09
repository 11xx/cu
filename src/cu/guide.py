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

There is no state to hold between calls except frame ids and, if you open one,
a session id — both live on disk. Every command takes --json.

OBSERVE BEFORE YOU LOOK
  cu observe --json              Monitors, windows, layer surfaces, geometry, focus, cursor.

  The compositor already knows which window is focused and where it sits, so
  this answers most of what a screenshot gets asked — which window is focused,
  whether the dialog opened, where the terminal is — exactly, and for a few
  hundred tokens. A bar is a layer surface, not a window, and `observe` reports
  it too. A click that lands on one reports that its geometry covers the point,
  not that it received the click: layer input regions may pass events through.
  A screenshot is for reading *content*, not for locating windows.

CAPTURE ONLY WHAT YOU NEED TO READ
  cu shot --window active        The usual first call.
  cu shot --output OUTPUT-A      A whole monitor.
  cu shot --region '100,100 400x200'
  cu shot --output OUTPUT-A --region '0,0 900x26'
  cu shot --if-changed           Reuse the last frame when the pixels match.
  cu shot --after f3 --watch '0,0 200x26'
                                 Recapture after those pixels change and settle.

  A window or landmark capture refuses with 27 if the target is off-workspace,
  hidden, or overlaps another visible window or a top/overlay layer. Focus
  history cannot prove stacking, so even a possibly-behind window is ambiguous.
  Capture never focuses or switches workspaces. Explicitly arrange the target,
  or use --output/--region for pixels that assert no particular window identity.

  A region is desktop-absolute by itself. With --output, its x,y are relative
  to that output; the returned logical_rect is always desktop-absolute.

  A capture prints a frame id and a path. Read the PNG at that path, along
  with what the image will cost whoever reads it. --if-changed prints the id it
  is reusing and nothing else, because no new image was written.

  After reloading a bar or other rendered content, use --after instead of a
  guessed sleep. The watch box is in the reference frame's image pixels and is
  required so an unrelated animation or ticking clock cannot satisfy the wait.
  The returned frame is the settled probe itself, with the elapsed time and
  probe count in its descriptor. Geometry and visibility are checked around
  every probe; moved, closed or hidden targets cannot inherit the old label.
  A wait costs captures; a tighter watch box is
  cheaper to compare. PNG decoding reconstructs pixels in native code; the
  entire image is decoded even when the watched box is small. Supported inputs
  are 8-bit non-interlaced grayscale, RGB, grayscale-alpha and RGBA PNGs, up to
  64 megapixels, with validated chunk CRCs and scanlines.

  Before paying for another capture, ask the frame already on disk:

  cu pixel --frame f3 --at 412,300       Exact r,g,b,a and #RRGGBBAA.
  cu pixel --frame f3 --at 412,300 --radius 2
                                          Median colour in the local box.
  cu magnify --frame f3 --scale 3         Nearest-neighbour derived frame.

  `pixel` reads image coordinates without producing an image. Its default is
  one exact pixel; --radius is an explicit hedge against antialiasing.
  `magnify` writes a new frame with more image pixels but the original capture
  time, monitor geometry, and logical rectangle. It makes small text readable without pretending
  the desktop was captured again, and reports the derived image's token cost.

  cu profiles                    What a capture costs each configured consumer.

  Image size is one of the two things you control that changes cost and
  latency, and consumers do not price it alike — they bill in patches, and the
  patch is not the same size everywhere. --for names the consumer, and a new
  one is a stanza under [capture.profiles] rather than a change to cu.

ACT THROUGH THE FRAME YOU READ
  cu focus zen
  cu move --frame f3 --at 412,300
  cu click --frame f3 --at 412,300
  cu scroll --frame f3 --at 412,300 --dy -3

  Coordinates are image pixels of a specific frame, always paired with its id.
  Never compute a screen coordinate yourself. `cu` owns every transform between
  image pixels, the compositor's logical space, and physical pixels — that
  arithmetic is the largest single source of confidently-wrong clicks, and it
  is already done for you.

  Keyboard commands require a focused window and recheck its live denylist
  state before sending keys. Pointer events recheck frame geometry after
  device settlement. Safety timing and drift limits must be finite and nonnegative.

  `focus` redirects keyboard actions without moving the pointer. `move` aims the
  pointer without pressing a button. `scroll` may aim through a frame first, or
  omit --frame and --at to send wheel events where the pointer already sits.

  Every injecting command takes --dry-run, which runs each interlock and writes
  the same log entry without emitting an event.

  A frame is bound to what it depicted, not only to where. A window that keeps
  its place while its content changes — a tab that navigated, a view that was
  replaced — refuses with 26 rather than clicking the old picture's coordinate
  on the new page. Capture again and decide again; the plan itself may no
  longer apply.

WAIT ON COMPOSITOR EVENTS, WATCH PIXELS FOR REPAINTS
  cu wait window-open --class zen --timeout 20

  `cu wait` handles focus, title, window, and workspace events. Those events do
  not prove rendered pixels have changed; use `cu shot --after` for a repaint.

EXIT CODES ARE THE CONTRACT
  Read them. They say what to do next, and the right response differs per code.
  `cu doctor --json` carries the full table, so it never has to be remembered.

   0  Fine                              Continue
   2  Malformed command                 Fix the arguments
  10  Environment unusable              Run `cu doctor`; retrying will not help
  20  Frame no longer describes screen  Capture a new one, retry
  21  Focus or target changed           Re-observe, then decide again
  22  Named target is gone              Re-observe or check the stored name
  23  Region crosses/is off an output   Aim wholly within one output
  24  Awaited event never arrived       Re-observe; it may have happened first
  25  Verification did not match state  Read actual state; do NOT blindly retry
  26  Same window, different content    Re-observe; the plan was about something else
  27  Hidden or obstructed capture      Arrange target explicitly, or capture an output
  28  Batch read-back unavailable       Inspect before continuing; never replay the prefix
  30  Denylist, lock, unreviewed recipe Do not retry; tell the user
  40  The user aborted                  Stop. Do not retry. Do not clear it.
  41  The user is moving the mouse      Yield; wait before retrying
  42  Another agent is driving          Wait for it to finish, then retry
  43  An agent tried to change abort    Ask the user to set or clear it
  44  Another agent reserved desktop    Wait for its session to end, then retry
  45  Reservation state unavailable     Stop; repair the registry before injecting
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
  an acting command's --why is one short phrase about that action's intent, not
  a restatement of the command: "accepting the fare", not "click at 412,300".
  status, session, and abort take no --why: they report or operate cu itself
  rather than doing something to the desktop, so there is no intent to show.
  Open a session for anything spanning more than a couple of commands —
  without one the indicator clears while you are thinking, which reads to the
  user as "it finished".

  `session begin` prints an id, and every command inside the session should say
  it belongs there: through CU_SESSION as above, or --session ID if you cannot
  set an environment variable between commands. It is never inferred, because
  you may not be the only agent on this desktop and guessing would file your
  actions under someone else's intent. An unnamed command still runs, filed as
  its own loose entry — except under a reservation, where an unnamed injecting
  command is refused with exit 44.

YOU MAY NOT BE ALONE HERE
  The desktop is one pointer, one keyboard focus, and one clipboard. `cu`
  serializes the commands that touch them, so only one agent injects at a time
  and the rest wait briefly. Past that wait you get exit 42 naming who holds
  it: that is another agent working, not a fault. Wait and retry.

  For a stretch of work that must not be changed between commands, start it
  with `cu session begin --exclusive --why 'purpose'`. Until that session ends,
  an injecting command must name its id through CU_SESSION or --session;
  another agent gets retryable exit 44 naming the reservation. A dead watched
  --pid or the normal session expiry also releases it. Ordinary sessions do
  not reserve anything.

  Reading is never serialized — observe, shot, pixel, magnify, wait, status,
  and doctor answer while someone else is driving, which is how you find out
  what is going on.
  `cu status` lists every agent currently driving, not just you.
  A status bar listens with `cu status --follow --json`: it receives one compact
  JSON object immediately and then only when that object changes. The feed
  checks the derived state every 250 ms, so that is its worst-case staleness.

TYPING AND KEYS
  cu type 'some text'            Clipboard by default; restores unless --no-restore.
  cu type 'some text' --verify   Read the field back and confirm it arrived.
  cu type 'new value' --replace --verify  Replace a populated field; exact read-back.
  cu type 'x' --method keys      Per-character, for fields that reject a paste.
  cu type '443' --frame f23 --at 455,320 --verify
  cu key --action paste          Semantic, resolved for the focused application.
  cu key --chord ctrl+alt+shift+f4

  Filling a visual form aims with the same command that types. A separate
  click releases the desktop before `type` takes it again, and in that gap
  focus can move inside the same window or the page can replace the control
  while the window's address and title stand still — after which the text goes
  somewhere nobody looked at. Given --frame and --at, `type` revalidates the
  target, clicks, confirms the click produced keyboard focus, injects, and
  verifies, all under one hold on the desktop.

  --replace selects the whole field after any aimed click, under the same
  desktop hold. It requires a known select-all binding and nonempty text;
  --replace --verify compares exact equality, while insertion verifies only
  containment. Replacement never submits the field. Read-back can still be
  unverified; inspect before submitting. Recipes retain replacement and verification.

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

SHORT, ALREADY-DECIDED ACTION BATCHES
  cu batch steps.json --json --why 'fill the inspected field'

  A batch file is a JSON array of argv arrays, without the leading cu:
  [["focus", "example-app"], ["type", "new value", "--replace", "--verify"]]

  At most 32 actions and 64 KiB; commands are focus, move, click, scroll, type,
  key and browser. No shell expansion, nested batch, recipe approval, abort
  mutation or stdin typing. Session, intent and dry-run belong to the outer
  command. All argument syntax is checked before the first action.

  One desktop hold covers the batch. Every action keeps its own interlocks;
  between steps, focus, geometry and content are checked against the established
  context. Only an explicit successful focus/click/type updates the target.
  Stop the batch where new visual judgment is needed; navigation is such a boundary.
  Dry-run checks current state, not a simulated future desktop.

  The result names completed steps and the failing step, and preserves its exit
  code. An unverified read-back stops with 28. A failed step may have partly
  acted; no rollback or automatic retry occurs. Never replay the successful prefix.
  Capture separately after the batch to decide what happens next.

BROWSERS
  cu browser list                What is installed, and what each resolves.
  cu browser actions             What the focused browser can be driven by.
  cu browser focus-urlbar
  cu browser close-tab

  Drive chrome by action and page content by coordinates. Actions resolve from
  each browser's own configuration; a browser that resolves nothing until the
  user configures it is being honest, not broken.

  A browser may be configured to keep its chrome hidden until the pointer
  reaches an edge, so an address bar missing from a capture may be revealable
  rather than absent. That is one instance of the pattern below.

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

CONTROLS THAT HIDE UNTIL THE POINTER ARRIVES
  cu know show <app>             Whether a reveal edge was learned here.
  cu move --frame f3 --at 640,2  Hover the learned edge, through the frame read.
  cu shot --after f3 --watch '0,0 1280x40'
  cu know note <app> 'the toolbar reveals on the top edge'

  A capture can honestly show an application while omitting controls it hides
  until the pointer comes near. Absence in the picture is then not evidence of
  absence — and guessing a keyboard chord instead is how a keystroke performs
  some other command.

  The revealing edge is learned state, not a default: which edge, and what sits
  behind it, are the user's configuration. Ask `cu know` first. If a placement
  is recorded, hover it through the frame you read, wait for the reveal with
  `cu shot --after` watching a narrow strip along that edge, and act only
  through the fresh frame that comes back — the revealed control does not exist
  in the frame you hovered from, and coordinates never cross frames. Record a
  placement you verify, so the next run does not rediscover it.

  If nothing is known, widen the capture rather than hovering each edge in
  turn. A probe per edge is four pointer moves on a live desktop to answer a
  question one --output capture may answer on its own.

THE INTERLOCKS, AND WHAT TO TELL THE USER
  `cu` refuses to type into password managers, lock screens, and windows whose
  title reads like a credential prompt. The list is a default the user's config
  can change. If it refuses, do not work around it.

  Recipes do not execute until a human has read and approved them. An unapproved
  recipe may be replayed only with --dry-run, which injects nothing. Key repeats
  survive promotion; typing with a mismatched read-back is not promoted. Show the
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

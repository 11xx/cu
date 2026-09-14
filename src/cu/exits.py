"""Exit codes, and the response each one asks of the caller.

The agent's correct reaction differs per refusal, so refusals do not share a
code. The distinction that matters most is retryable versus not: treating
ABORTED as transient would defeat the one interlock the agent cannot clear.
"""

from __future__ import annotations

OK = 0
ERROR = 1
USAGE = 2

PRECONDITION_FAILED = 10
STALE_FRAME = 20
FOCUS_CHANGED = 21
TARGET_NOT_FOUND = 22
DEGENERATE_AIM = 23
TIMED_OUT = 24
VERIFY_FAILED = 25
CONTENT_CHANGED = 26
TARGET_NOT_VISIBLE = 27
VERIFY_UNAVAILABLE = 28
CAPTURE_BUDGET_EXCEEDED = 29
DENIED_BY_POLICY = 30
TARGET_AMBIGUOUS = 31
MOTION_NOT_CONVERGED = 32
MOTION_UNSAFE_ROUTE = 33
UNREACHABLE_AIM = 34
PRESENTATION_REFUSED = 35
PRESENTATION_PARTIAL = 36
PRESENTATION_CHANGED = 37
PRESENTATION_STALE = 38
PRESENTATION_TOKEN = 39
ABORTED_BY_USER = 40
HUMAN_ACTIVE = 41
DESKTOP_BUSY = 42
ABORT_CONTROLLED_BY_USER = 43
DESKTOP_RESERVED = 44
RESERVATION_UNAVAILABLE = 45
NOT_MAPPED = 50
BROWSER_READ_UNAVAILABLE = 51
BROWSER_DOCUMENT_STALE = 52
BROWSER_READ_PROTOCOL = 53
BROWSER_READ_TIMEOUT = 54
BROWSER_READ_AMBIGUOUS = 55
BROWSER_READ_REFUSED = 56
BROWSER_READ_BUDGET = 57
BROWSER_ACTION_UNCERTAIN = 58

#: What a caller should do on each code. Shipped in `cu doctor --json` and
#: summarized in the bare-`cu` guide, so the contract has exactly one source.
RESPONSE = {
    OK: "Proceed.",
    ERROR: "Unexpected failure. Read stderr; do not blindly retry.",
    USAGE: "The command was malformed. Fix the arguments.",
    PRECONDITION_FAILED: (
        "The environment is not usable. Run `cu doctor` and resolve it; retrying will not help."
    ),
    STALE_FRAME: "The frame no longer describes the screen. Capture a new one and retry.",
    FOCUS_CHANGED: "Focus moved between planning and acting. Re-observe, then decide again.",
    TARGET_NOT_FOUND: (
        "The named window, output, element, browser, or recipe is gone. "
        "Re-observe or check the stored name."
    ),
    DEGENERATE_AIM: (
        "The region spans outputs or is empty or outside every output. "
        "Aim wholly within one output."
    ),
    TIMED_OUT: (
        "The awaited event did not arrive in time. Re-observe: it may have "
        "happened before the wait started, or not at all."
    ),
    VERIFY_FAILED: (
        "Verification did not match the actual state. Read the actual state before retrying; "
        "retrying blindly can repeat what did land."
    ),
    CONTENT_CHANGED: (
        "The window still holds the frame's place, but shows different content. "
        "Re-observe and decide again: the plan was made about something else."
    ),
    TARGET_NOT_VISIBLE: (
        "The requested window is hidden or its capture is obstructed. "
        "Observe, explicitly focus or rearrange it, or capture an output without window identity."
    ),
    VERIFY_UNAVAILABLE: (
        "The requested read-back could not establish the result. "
        "Inspect the field before continuing; do not replay the completed prefix."
    ),
    CAPTURE_BUDGET_EXCEEDED: (
        "The encoded image exceeds the capture profile's byte limit. "
        "Request a smaller region or a lower-resolution profile."
    ),
    TARGET_AMBIGUOUS: (
        "The intended recipient cannot be established at the aimed point. "
        "Re-observe and arrange the target or choose an unambiguous point."
    ),
    MOTION_NOT_CONVERGED: (
        "Native pointer movement did not reach its endpoint within the bound. "
        "Inspect the pointer and device state before trying again."
    ),
    MOTION_UNSAFE_ROUTE: (
        "Native movement leaves the inspected safe region. "
        "Capture a wider region or place the pointer inside it first."
    ),
    UNREACHABLE_AIM: (
        "The aimed point is outside the union of active outputs. "
        "Choose a reachable point or explicitly change the desktop layout."
    ),
    PRESENTATION_REFUSED: (
        "The compositor refused the requested presentation or its read-back was unchanged. "
        "Inspect the actual state before trying again."
    ),
    PRESENTATION_PARTIAL: (
        "Presentation or restoration was only partly applied. "
        "Inspect the actual window state; do not replay blindly."
    ),
    PRESENTATION_CHANGED: (
        "The window changed after presentation. Restoration was refused and "
        "the current state was preserved."
    ),
    PRESENTATION_STALE: (
        "The presentation token's window identity is stale or unknown. "
        "Re-observe and present again."
    ),
    PRESENTATION_TOKEN: (
        "The presentation token is missing, invalid, or already consumed. "
        "Re-observe and present again."
    ),
    DENIED_BY_POLICY: "Refused by the denylist or a locked session. Do not retry; tell the user.",
    ABORTED_BY_USER: (
        "The user aborted. Stop. Do not retry, and do not attempt to clear the abort."
    ),
    HUMAN_ACTIVE: (
        "The user is moving the physical mouse. Yield; wait for them to stop before retrying."
    ),
    DESKTOP_BUSY: (
        "Another agent is driving the desktop. Wait for it to finish and retry; "
        "acting now would interleave with its pointer, focus, and clipboard."
    ),
    ABORT_CONTROLLED_BY_USER: ("The abort flag belongs to the user. Ask them to set or clear it."),
    DESKTOP_RESERVED: (
        "Another agent has reserved the desktop. Wait for its session to end and retry, "
        "or tell the user who holds it."
    ),
    RESERVATION_UNAVAILABLE: (
        "The desktop reservation state is unavailable. Do not inject or retry; "
        "the registry must be repaired first."
    ),
    BROWSER_READ_UNAVAILABLE: "Grant the tab from the CU Browser extension, or use pixel capture.",
    BROWSER_DOCUMENT_STALE: "Browser document/generation changed. Read again and reconsider.",
    BROWSER_READ_PROTOCOL: "Protocol mismatch. Check extension and native-host versions.",
    BROWSER_READ_TIMEOUT: "Browser observation timed out. Inspect the tab and helper state.",
    BROWSER_ACTION_UNCERTAIN: (
        "Action may have applied. Inspect fresh evidence; never blindly replay."
    ),
    BROWSER_READ_AMBIGUOUS: "Select an explicit browser helper and granted tab; do not guess.",
    BROWSER_READ_REFUSED: "Read not authorized/supported. Inspect the grant; do not bypass it.",
    BROWSER_READ_BUDGET: "Evidence exceeds its bound. Narrow the query or reduce the limit.",
    NOT_MAPPED: (
        "No known binding for this action in this application. Do not guess a default chord."
    ),
}

#: Codes where retrying the same command can succeed once the world changes.
#: ABORTED_BY_USER is excluded deliberately: an agent that retries it has
#: defeated the one interlock the user holds and the agent cannot clear.
RETRYABLE = frozenset(
    {
        STALE_FRAME,
        FOCUS_CHANGED,
        CONTENT_CHANGED,
        TARGET_NOT_FOUND,
        TARGET_AMBIGUOUS,
        MOTION_NOT_CONVERGED,
        MOTION_UNSAFE_ROUTE,
        UNREACHABLE_AIM,
        TIMED_OUT,
        HUMAN_ACTIVE,
        DESKTOP_BUSY,
        DESKTOP_RESERVED,
    }
)


def name(code: int) -> str:
    for key, value in globals().items():
        if key.isupper() and value == code and isinstance(value, int):
            return key
    return "UNKNOWN"

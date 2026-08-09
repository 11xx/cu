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
DENIED_BY_POLICY = 30
ABORTED_BY_USER = 40
HUMAN_ACTIVE = 41
DESKTOP_BUSY = 42
ABORT_CONTROLLED_BY_USER = 43
DESKTOP_RESERVED = 44
NOT_MAPPED = 50

#: What a caller should do on each code. Shipped in `cu doctor --json` and
#: summarized in the bare-`cu` guide, so the contract has exactly one source.
RESPONSE = {
    OK: "Proceed.",
    ERROR: "Unexpected failure. Read stderr; do not blindly retry.",
    USAGE: "The command was malformed. Fix the arguments.",
    PRECONDITION_FAILED: (
        "The environment is not usable. Run `cu doctor` and resolve it; "
        "retrying will not help."
    ),
    STALE_FRAME: "The frame no longer describes the screen. Capture a new one and retry.",
    FOCUS_CHANGED: "Focus moved between planning and acting. Re-observe, then decide again.",
    TARGET_NOT_FOUND: "The named window, output, or element is gone. Re-observe.",
    DEGENERATE_AIM: (
        "The region spans outputs or falls outside any window. "
        "Widen to --window or --output."
    ),
    TIMED_OUT: (
        "The awaited event did not arrive in time. Re-observe: it may have "
        "happened before the wait started, or not at all."
    ),
    VERIFY_FAILED: (
        "Verification did not match the actual state. Read the actual state before retrying; "
        "retrying blindly can repeat what did land."
    ),
    DENIED_BY_POLICY: "Refused by the denylist or a locked session. Do not retry; tell the user.",
    ABORTED_BY_USER: (
        "The user aborted. Stop. Do not retry, and do not attempt to clear the abort."
    ),
    HUMAN_ACTIVE: (
        "The user is moving the physical mouse. Yield; wait for them to stop "
        "before retrying."
    ),
    DESKTOP_BUSY: (
        "Another agent is driving the desktop. Wait for it to finish and retry; "
        "acting now would interleave with its pointer, focus, and clipboard."
    ),
    ABORT_CONTROLLED_BY_USER: (
        "The abort flag belongs to the user. Ask them to set or clear it."
    ),
    DESKTOP_RESERVED: (
        "Another agent has reserved the desktop. Wait for its session to end and retry, "
        "or tell the user who holds it."
    ),
    NOT_MAPPED: (
        "No known binding for this action in this application. "
        "Do not guess a default chord."
    ),
}

#: Codes where retrying the same command can succeed once the world changes.
#: ABORTED_BY_USER is excluded deliberately: an agent that retries it has
#: defeated the one interlock the user holds and the agent cannot clear.
RETRYABLE = frozenset(
    {
        STALE_FRAME,
        FOCUS_CHANGED,
        TARGET_NOT_FOUND,
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

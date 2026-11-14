"""The guide: what a session needs before its first command, and the depth behind it.

Bare `cu` prints the map, which is bounded so that it is read whole. `cu guide`
indexes the topics and prints one at a time; each topic is bounded too. The
text lives in `guide_text/`. Anything that is one command's contract stays in
that command's `--help`.

Every claim in the map and the topics must be traceable to code.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

#: The most lines and bytes the map may hold, and the most bytes one topic may
#: hold. A topic that outgrows its bound is split, never trimmed.
MAP_MAX_LINES = 80
MAP_MAX_BYTES = 6144
TOPIC_MAX_BYTES = 16384

#: The matching lines a search shows for one route before it only counts them.
SEARCH_CONTEXT_LINES = 3
SEARCH_LINE_WIDTH = 160

_TEXT = Path(__file__).parent / "guide_text"


def _read(name: str) -> str:
    return (_TEXT / f"{name}.txt").read_text(encoding="utf-8")


@dataclass(frozen=True)
class Topic:
    name: str
    summary: str

    @property
    def body(self) -> str:
        return _read(self.name)


GROUPS: tuple[tuple[str, tuple[Topic, ...]], ...] = (
    ("See, then act", (
        Topic("observe", "What the compositor already knows, and checking you can see a capture"),
        Topic("capture", "Frames, regions, waits on repaint, pixel reads and what an image costs"),
        Topic("act", "Focus, pointer, scroll and presentation through a frame; compositor waits"),
        Topic("postcondition", "Choosing the fact that proves an action, and windows that appear"),
        Topic("type", "Typing, read-back, replacement and semantic key actions"),
        Topic("batch", "Several already-decided actions under one hold"),
    )),
    ("When something refuses or goes wrong", (
        Topic("exits", "Every exit code with its response, and the user's abort"),
        Topic("recover", "What to do at the first uncertain step, case by case"),
        Topic("interlocks", "What cu refuses, recipes, the sandbox, and what to tell the user"),
    )),
    ("Working beside the user and other agents", (
        Topic("session", "Sessions, --why, the status bar, reservations and other agents"),
        Topic("effort", "Reasoning effort and capture size as separate costs"),
    )),
    ("Browsers", (
        Topic("browser", "Native browser actions resolved from each browser's configuration"),
        Topic("web", "The optional extension: grants, reads, actions, waits and capture"),
    )),
    ("Learned state and practice", (
        Topic("know", "Landmarks, notes, host facts and controls that hide until hovered"),
        Topic("playground", "The local practice page and its task fixtures"),
    )),
)

TOPICS: dict[str, Topic] = {
    topic.name: topic for _, topics in GROUPS for topic in topics
}

MAP = _read("map")


def render() -> str:
    """What bare `cu` prints."""
    return MAP


def index() -> str:
    lines = ["Guide topics, one at a time with `cu guide <topic>`:"]
    for title, topics in GROUPS:
        lines += ["", title]
        lines += [f"  cu guide {topic.name:<14} {topic.summary}" for topic in topics]
    lines += [
        "",
        "  cu guide --search TEXT    The topics and command helps that mention TEXT.",
        "  cu guide --all            Every topic at once: long, only when asked for.",
        "  cu <command> --help       A command's full contract.",
    ]
    return "\n".join(lines) + "\n"


def full() -> str:
    """Every topic, each exactly as it prints alone."""
    return "\n".join(topic.body for topic in TOPICS.values())


def _help_routes(parser: argparse.ArgumentParser, path: str):
    """Each subcommand's rendered help, however deeply it nests."""
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, child in action.choices.items():
            child_path = f"{path} {name}"
            yield f"{child_path} --help", child.format_help()
            yield from _help_routes(child, child_path)


def _excerpt(line: str, wanted: str) -> str:
    """One line of context, cut to a window around the match when it is long."""
    if len(line) <= SEARCH_LINE_WIDTH:
        return line
    at = max(line.lower().find(wanted), 0)
    start = min(max(at - SEARCH_LINE_WIDTH // 4, 0), len(line) - SEARCH_LINE_WIDTH)
    window = line[start:start + SEARCH_LINE_WIDTH]
    return ("…" if start else "") + window + ("…" if start + SEARCH_LINE_WIDTH < len(line) else "")


def search(parser: argparse.ArgumentParser, needle: str) -> str:
    """The routes whose text holds `needle`, each with its first matching lines.

    The match is literal and ignores case. No route's whole text is returned.
    """
    wanted = needle.lower()
    routes = [("cu", MAP)]
    routes += [(f"cu guide {topic.name}", topic.body) for topic in TOPICS.values()]
    routes += list(_help_routes(parser, "cu"))
    out: list[str] = []
    for command, text in routes:
        matching = [line.strip() for line in text.splitlines() if wanted in line.lower()]
        if not matching:
            continue
        out.append(command)
        out += [f"    {_excerpt(line, wanted)}" for line in matching[:SEARCH_CONTEXT_LINES]]
        if len(matching) > SEARCH_CONTEXT_LINES:
            out.append(f"    … {len(matching) - SEARCH_CONTEXT_LINES} more matching lines")
    if not out:
        return (
            f"No guide topic or command help mentions `{needle}`. "
            "`cu guide` lists the topics.\n"
        )
    return "\n".join(out) + "\n"


def answer(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """What `cu guide` prints for the parsed arguments."""
    if args.search is not None:
        return search(parser, args.search)
    if args.all:
        return full()
    if args.topic is None:
        return index()
    return TOPICS[args.topic].body

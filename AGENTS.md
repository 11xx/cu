# ai-agent-computer-use

`cu`: a deterministic CLI that lets a multimodal agent see the screen and drive
the pointer, keyboard, windows, and browsers of a real Linux/Wayland desktop,
targeting Hyprland. Non-goals: no daemon, no overlay, no prompt layer.

## Invariants

- **`cu` owns every coordinate transform.** Image pixels, the compositor's
  logical space, and physical pixels are three different things, and mixing
  them does not error — it clicks confidently on the wrong pixel. A caller
  names a frame id and image coordinates; it never computes a screen position.
- **Exit codes are the contract.** Each refusal gets its own code because the
  correct reaction differs per refusal. `exits.RESPONSE` is the single source;
  `cu doctor --json` ships it. Adding a failure mode means adding a code, not
  reusing a near-miss.
- **Nothing guesses.** An action absent from the keymap resolves to nothing and
  the caller refuses, rather than falling back to a documented default — the
  keystroke would arrive and would do something else. Same for a landmark that
  cannot be honestly projected: it reports itself stale.
- **The visible desktop is the default, and that is a safety property.** The
  user can watch and stop the agent. Working where they cannot see requires
  `cu sandbox`, which is a deliberate, named act.
- **The abort flag is the user's alone.** Exit 40 is never retryable and
  nothing in this repository clears it on their behalf.
- **State lives outside the repository.** Config in `$XDG_CONFIG_HOME/cu/`,
  recipes and learned knowledge in `$XDG_DATA_HOME/cu/`, frames, status, and
  the abort flag in `$XDG_RUNTIME_DIR/cu/`. Nothing host-shaped is committed.

## Working here

- **The CLI is the only workflow surface.** Bare `cu` teaches the loop and the
  judgment around it (`src/cu/guide.py`); `--help` carries each command's
  contract. There is no external skill or `AGENTS.md` fragment to fall back on,
  which means a behavior change updates the guide in the same commit — nothing
  else will catch the drift.
- **Every claim in the guide must be traceable to code.** A guide that promises
  more than the tool does is worse than none, because it is believed. Check the
  claim against the source before writing it, and cut what cannot be traced
  rather than softening it.
- **State the invariant, not this host's reading.** Documentation describes how
  the compositor behaves and why the code is shaped around it. What *this*
  machine measures — versions, outputs, installed tooling — is what `cu doctor`
  reports at runtime; it does not belong in a tracked file.
- **The gates.** `uv run pytest` and `uv run ruff check .`; both tools and the
  rules they enforce are pinned in `pyproject.toml` so they report the same
  thing on any host.
- **Silent failures are the whole subject.** Most of what this tool exists to
  prevent fails by producing something plausible at exit 0 — a dispatcher form
  that constructs and discards, a crop aimed at the wrong place, a paste an
  application rejected. A probe that cannot distinguish working from no-op has
  measured nothing; make it move something it was not already at.
- **Verify a claim about the compositor before writing it into code, a comment,
  or `docs/`.** These interfaces are undocumented and drift.
- Run non-trivial changes through `arc`; gates are `uv run pytest` and
  `uv run ruff check .`.

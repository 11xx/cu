# Local publication demo

[`demo.gif`](demo.gif) is a private-safe, two-frame illustration of a
disposable GTK target on a named headless output. The frames contain only the
target heading, its state, one button, and the pointer in the after frame. The
target writes a `false` marker before it starts and a `true` marker from the
button callback. The changed label and marker are application postconditions;
the pointer position and click receipt alone are not.

The target is [`demo-target.py`](demo-target.py). Run `bin/cu doctor --json`
first and resolve any failed precondition. This native GTK flow does not load
the Firefox extension fixture, grant a browser tab, or launch a worker. A short
run with explicit startup and cleanup is below. In addition to the doctor
checks, it needs `jq` and a Python 3 installation with GTK 4 PyGObject; those
demo helpers are not `cu` runtime prerequisites.

The target must open on the headless output's workspace, which the sandbox
reports as `active_workspace`. `cu` does not move windows between outputs, so
that placement belongs to the compositor's own rules (for example a window
rule matching the title `CU demo`). The script checks where the target landed
and stops, closing it, before any capture or click if it opened anywhere else.

```sh
set -eu
demo_dir=$(mktemp -d)
export CU_DEMO_RESULT="$demo_dir/result.json"

headless=""
target_pid=""
cleanup() {
  if test -n "$target_pid"; then
    kill "$target_pid" 2>/dev/null || true
    wait "$target_pid" 2>/dev/null || true
  fi
  if test -n "$headless"; then
    cu sandbox remove "$headless" >/dev/null 2>&1 || true
  fi
  rm -rf "$demo_dir"
}
trap cleanup EXIT INT TERM

sandbox_json=$(cu sandbox create --json)
headless=$(printf '%s\n' "$sandbox_json" | jq -r '.name // empty')
headless_workspace=$(printf '%s\n' "$sandbox_json" | jq -r '.active_workspace // empty')
test -n "$headless" && test -n "$headless_workspace"

# The compositor's placement rules must map this window on "$headless_workspace".
python3 docs/demo-target.py &
target_pid=$!
cu wait window-open --title 'CU demo' --json
placed=$(cu observe --json |
  jq -r --argjson pid "$target_pid" '[.windows[] | select(.pid == $pid)][0].monitor // empty')
test "$placed" = "$headless"
ready_json=$(cu shot --output "$headless" --target-window 'CU demo' --json)
ready_frame=$(printf '%s\n' "$ready_json" | jq -r '.id // empty')
test -n "$ready_frame"
test "$(jq -r .clicked "$CU_DEMO_RESULT")" = false

# Read ready_frame and use the visible Verify click coordinate from that frame.
cu click --frame "$ready_frame" --at BUTTON_X,BUTTON_Y --json
test "$(jq -r .clicked "$CU_DEMO_RESULT")" = true
cu shot --output "$headless" --target-window 'CU demo' --json
```

`ready_frame` and `headless` are readings from that run; use the values returned
by each command. Replace `BUTTON_X,BUTTON_Y` with the coordinate read from
`ready_frame` (the retained 1920x1080 illustration uses `960,320`). `cu click`
owns the image-to-compositor transform and its receipt records the exact frame
coordinate and resolved target. The false-marker check, true-marker check, and
second capture are the verification boundary. Inspect every fresh capture
before retaining it; the checked-in GIF is not a current-host acceptance
result.

The demo is local preparation only. It does not create a remote, publish an
artifact, install a package, or sign a store artifact, and it captures and
clicks only on the headless output. A worker/controller comparison is a separately authorized
experimental trial, not a capability provided by this demo.

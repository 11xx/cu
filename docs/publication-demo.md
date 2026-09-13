# Local publication demo

[`demo.gif`](demo.gif) is a private-safe, two-frame illustration of a
disposable GTK target on a named headless output. The frames contain only the
target heading, its state, one button, and the pointer in the after frame. The
target writes a `false` marker before it starts and a `true` marker from the
button callback. The changed label and marker are application postconditions;
the pointer position and click receipt alone are not.

The target is [`demo-target.py`](demo-target.py). Run `bin/cu doctor --json`
first and resolve any failed precondition. Keep the result file in a scratch
directory under the home filesystem. This native GTK flow does not load the
Firefox extension fixture, grant a browser tab, or launch a worker. A short
run with explicit startup and cleanup is below. In addition to the doctor
checks, it needs `jq` and a Python 3 installation with GTK 4 PyGObject; those
demo helpers are not `cu` runtime prerequisites.

```sh
set -eu
demo_dir="$HOME/.worktrees/cu-publication-demo"
mkdir -p "$demo_dir"
export CU_DEMO_RESULT="$demo_dir/result.json"
rm -f "$CU_DEMO_RESULT"

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
}
trap cleanup EXIT INT TERM

sandbox_json=$(cu sandbox create --json)
headless=$(printf '%s\n' "$sandbox_json" | jq -r '.name // empty')
test -n "$headless"

python3 docs/demo-target.py &
target_pid=$!
cu wait window-open --title 'CU demo' --json
cu observe --json
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
artifact, install a package, sign a store artifact, or use the user's visible
outputs. A worker/controller comparison is a separately authorized
experimental trial, not a capability provided by this demo.

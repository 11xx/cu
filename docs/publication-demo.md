# Local publication demo

[`demo.gif`](demo.gif) shows a disposable GTK target on a named headless
output. The two captured states contain only the target heading, its state, and
one button. The second state is backed by the target's own JSON marker, so the
changed label is an application postcondition rather than a claim inferred from
an input receipt.

The target is [`demo-target.py`](demo-target.py). Keep its result file in a
scratch directory under the home filesystem. A short run is:

```sh
demo_dir="$HOME/.worktrees/cu-publication-demo"
mkdir -p "$demo_dir"
export CU_DEMO_RESULT="$demo_dir/result.json"
cu sandbox create --json
# Launch docs/demo-target.py on the returned headless workspace.
cu observe --json
cu shot --output HEADLESS-1 --json
cu click --frame FRAME_ID --at 960,320 --json
test "$(jq -r .clicked "$CU_DEMO_RESULT")" = true
cu shot --output HEADLESS-1 --json
cu sandbox remove HEADLESS-1
```

The frame id and headless output name are readings from that run; use the
values returned by each command. `cu click` owns the image-to-compositor
transform and its receipt records the exact frame coordinate and resolved
window. The marker check and the second capture are the verification boundary.

The demo is local preparation only. It does not create a remote, publish an
artifact, install a package, or use the user's visible outputs.

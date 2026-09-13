# Bounded evidence export

`cu export` is an opt-in read-only boundary for a frame that already exists in
CU's runtime store. It validates the descriptor, PNG dimensions, byte count, and
SHA-256 before returning a `cu-evidence/1` envelope. The envelope preserves the
source/frame identity, capture time, producer/profile, requested and returned
coverage, image and logical coordinate spaces, monitor/window/target geometry,
and the runtime retention policy. A missing file says that runtime bytes are
unavailable and that collection time is unknown.

Reference-only export copies no bytes. `--retain PATH` writes validated bytes
once to a new caller-selected path and includes its digest. `--compare FRAME`
adds a same-coverage pixel-change observation whose score means SHA-256 equality;
it does not claim OCR, application state, or a live actionable frame.

```sh
cu export --frame f3 --compare f2 --json
cu export --frame f3 --retain ./evidence/f3.png --json
```

The local consumer fixture in `tests/evidence_consumer.py` checks the envelope,
coordinate spaces, and retained digest. Export never focuses, takes the desktop
lock, moves the pointer, touches the clipboard, or injects input. Browser CSS
rectangles and imported historical/video frames remain outside this contract.

# Bounded evidence export

`cu export` is an opt-in read-only boundary for a frame that already exists in
CU's runtime store. It validates the descriptor's source identity, dimensions,
logical coverage, coordinate spaces, byte count, and SHA-256 before reading the
PNG. The read is bounded to `MAX_RETAINED_BYTES + 1`, so an oversized source is
refused without reading it into an unbounded buffer. The resulting `cu-evidence/1`
envelope preserves the source/frame identity, capture time, producer/configuration,
requested and returned coverage, image and logical coordinate spaces,
monitor/window/target geometry, and the runtime retention policy. A missing file
says that runtime bytes are unavailable and that collection time is unknown.

Reference-only export copies no bytes. `--retain PATH` writes validated bytes
once to a new caller-selected path and includes its digest; validation of every
compare input completes before that path is created, and a failed write removes
its partial file. `--compare FRAME` requires the same source identity, logical
coverage, dimensions, and coordinate spaces. Its status is based on decoded RGBA
pixel equality and its score is named `decoded-pixel-equality`; the separate
`byte_hash_equal` field records whether the encoded PNG bytes also matched. It
does not claim OCR, application state, or a live actionable frame.

Derived observations carry the exact input frame IDs. The producer block carries
the CU version and capture configuration, while source availability distinguishes
runtime bytes, an explicit retained copy, and an unavailable reference. A
retained historical copy remains inspectable after the runtime frame is collected;
the retention policy does not provide an exact collection timestamp.

```sh
cu export --frame f3 --compare f2 --json
cu export --frame f3 --retain ./evidence/f3.png --json
```

The local consumer fixture in `tests/evidence_consumer.py` checks the envelope,
coordinate spaces, coverage, provenance, derived input references, and retained
digest; it is a local fixture, not an adapter for any other product. Export
never focuses, takes the desktop lock, moves the pointer, touches the clipboard,
or injects input. Browser CSS rectangles and imported historical/video frames
remain outside this contract.

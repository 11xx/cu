"""Synthetic caller-visible wait benchmark; run with `uv run python tests/benchmark_observation.py`.

Acquisition writes a fixed PNG, not a desktop capture. Times include baseline
and probe decoding and a 200 ms quiet interval, but exclude model transport.
"""

import contextlib
import io
import json
import os
import statistics
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from cu import capture, cli, config, frames, observe, png


def run(width=1920, height=1058, repeats=3):
    baseline = png.encode(width, height, bytes((30, 60, 90, 255)) * width * height)
    changed = png.encode(width, height, bytes((90, 60, 30, 255)) * width * height)
    samples = []
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        "XDG_RUNTIME_DIR": directory, "XDG_CONFIG_HOME": directory,
    }):
        rect = {"x": 0, "y": 0, "w": width, "h": height}
        monitor = {"name": "fixture", "logical_rect": rect, "scale": 1}
        snap = {"monitors": [monitor], "windows": [], "focused": None,
                "cursor_logical": {"x": 0, "y": 0}, "layer_surfaces": []}
        with frames.capture_scratch() as scratch:
            scratch.write_bytes(baseline)
            reference = frames.new(
                source={"type": "region", "name": "fixture"}, logical_rect=rect,
                image_size={"w": width, "h": height}, scratch=scratch,
                monitor=monitor, capture_scale=1, profile="generic",
            )
        with (
            patch.object(observe, "snapshot", return_value=snap),
            patch.object(observe, "cursor", return_value=snap["cursor_logical"]),
            patch.object(capture, "grab_region",
                         side_effect=lambda r, p, s: p.write_bytes(changed)),
            patch.object(config, "log_path", return_value=Path(directory) / "actions.jsonl"),
        ):
            for _ in range(repeats):
                output = io.StringIO()
                started = time.monotonic()
                with contextlib.redirect_stdout(output):
                    code = cli.main([
                        "shot", "--after", reference["id"], "--watch", "0,0 1x1",
                        "--quiet-ms", "200", "--poll-ms", "100", "--timeout", "30", "--json",
                    ])
                elapsed = time.monotonic() - started
                assert code == 0
                result = json.loads(output.getvalue())
                assert Path(result["path"]).read_bytes() == changed
                assert result["logical_rect"] == rect
                samples.append({"seconds": elapsed, "probes": result["wait"]["probes"]})
    return {"image_size": [width, height], "encoded_bytes": len(changed),
            "quiet_ms": 200, "samples": samples,
            "median_seconds": statistics.median(s["seconds"] for s in samples)}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))

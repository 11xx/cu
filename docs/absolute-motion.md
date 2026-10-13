# Absolute motion planning

`cu.absolute_motion` provides pure stream plans. A `Layout` holds immutable,
normalized logical output rectangles, including negative origins. Axis values
use the layout's bounding rectangle and 65,536 quantization steps. Logical pixel
centers select bins, saturating at the upper axis bound before integer conversion.
Points outside the active output union refuse, including
empty regions inside the bounding rectangle. A complete straight route must be
covered by that union; sparse samples cannot hide an intervening gap.

`plan` samples a straight route at an integer multiple of a supplied output
refresh rate. It preserves deadlines for duplicate positions and keeps an exact
terminal deadline and endpoint when the duration is not a whole event interval.
Minimum-jerk easing changes positions at the same deadlines. Rates, duration and
sample counts are bounded, with at least one interval between start and endpoint.
The resulting samples contain logical and encoded
coordinates but carry no device, compositor or input authority.

The CLI's pointer carrier uses relative events. Absolute planning is not selected
by an input command. An absolute carrier needs live evidence for axis mapping,
Chromium hover, relative-pointer interplay and layout changes during a stream
before selection. Runtime supervision must recheck layout identity and the
planned position at frame rate, yield on non-CU pointer events, and report an
inference fallback when physical pointer nodes cannot be read. Physical hand
latency and remapper forwarding need acceptance with a person moving the mouse.

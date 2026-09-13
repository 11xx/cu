# Browser-frame association probe

The current browser evidence and compositor evidence have separate identities.
Browser reads expose a tab/document/generation and a CSS viewport. CU observes a
compositor window address, class, logical rectangle, and output geometry. A
matching title or URL is not a shared identity: two tabs can show the same page,
and one tab can reload that page at the same URL.

The read-only probe in `tests/browser_frame_probe.py` produces a bounded verdict:

```sh
uv run pytest tests/test_browser_frame_association.py -q
```

Without a stable browser-supplied compositor-window bridge it returns
`unprojectable`. Multiple candidates sharing a claimed bridge return
`ambiguous`. The smallest supported domain is one unique bridge plus a viewport
and compositor logical rectangle; its invalidation set is document ID,
generation, window address, and geometry. Scroll, zoom, decorations, sidebars,
fractional output scale, hidden/inactive windows, movement, replacement, and
cross-origin or unsupported geometry require fresh evidence and do not inherit a
mapping.

The compact evidence shape is `cu-browser-frame-association/1` with browser
document/generation/viewport, compositor address/rectangle/output, mapping
status, and invalidation reasons. It carries no page body, URL, screenshot, or
native action. A current host query with no helper therefore remains an
unprojectable read, not a guessed coordinate transform.

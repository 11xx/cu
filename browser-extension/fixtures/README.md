# Browser interaction fixture

`control.html` contains disposable controls, sensitive-field labels, delayed DOM
readiness, replacement targets, application/event counters, a timer-reset hover
target whose controls begin hidden, duration-labelled result cards, a scroll pane, and
muted media controls. Its values are synthetic. It does not submit data or
contact external services.

Run from the repository root. Generate the optional four-second media clip in a
private runtime directory; do not commit generated media:

```sh
fixture=$(mktemp -d "${XDG_RUNTIME_DIR:?}/cu-fixture.XXXXXX")
cp browser-extension/fixtures/control.html "$fixture/index.html"
ffmpeg -f lavfi -i color=c=blue:s=320x180:r=10 -t 4 -an \
  -c:v libvpx -b:v 100k "$fixture/media.webm"
python -m http.server 8000 --bind 127.0.0.1 --directory "$fixture"
```

Choose an unused local port. Open the fixture in the authorized browser window
and explicitly select the required CU Browser grant mode. Read-only grants must
refuse actions. Stop before choosing interaction mode.

Use current document, generation, and element refs. Fill a value different from
its initial value, then read it back. Verify clicks, native pointer entry, and
synthetic hover through the counters rather than their delivery receipts. A
`cu hover` pixel change shows that the page repainted; inspect the returned frame
before calling the control revealed. Programmatic field changes
and replacement targets must invalidate previously observed refs; sensitive,
disabled, hidden, and covered controls must refuse interaction. Wait for delayed
readiness and advancing media time instead of sleeping. Candidate labels on the
result cards are not verified media durations.

Compare the optional cursor on the visible desktop with a cursor-free browser
capture. Test inactive-tab refusals, Stop cancellation, and reload revocation.
When finished, revoke the grant, close disposable tabs, stop the server, and
remove the runtime fixture directory.

# Browser interaction fixture

`control.html` contains disposable controls including an ordinary single-select,
disabled and sensitive select options, sensitive-field labels, delayed
DOM readiness, replacement targets, application/event counters, a timer-reset
hover target whose controls begin hidden, a four-item collection with
hover-only selection and a bulk action, a no-bulk single-item example,
duration-labelled result cards, a scroll pane and muted media controls. Its
values are synthetic. It does not submit data or contact external services.

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

Use current document, generation and element refs. Fill a value different from
its initial value, then read it back. Verify clicks, native pointer entry and
synthetic hover through the counters rather than their delivery receipts. For
the ordinary select, choose an option by its exact value or visible label and
read back the resulting selection; disabled and sensitive select controls must
refuse without dispatch. For the collection, hover one item first, then use the
select-all control and verify the selected count before the bulk action. Item 3
deterministically reports a
partial failure so recovery can return to that item without replaying the whole
batch. Programmatic field changes and replacement targets must invalidate
previously observed refs; sensitive, disabled, hidden and covered controls must
refuse interaction. Wait for delayed readiness and advancing media time instead
of sleeping. Candidate labels on result cards are not verified media durations.

Compare the optional cursor on the visible desktop with a cursor-free browser
capture. Request a small CSS viewport region over a known nonsensitive part of
the fixture and verify its returned coverage and pixels against the full
viewport. Test inactive-tab refusals, scale/resize drift, Stop cancellation and
reload revocation. A mocked crop does not establish Firefox rendering or
capture timing; retain live acceptance as separate coverage evidence.
When finished, revoke the grant, close disposable tabs, stop the server and
remove the runtime fixture directory.

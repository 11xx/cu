import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";
import collectorModule from "./collector.js";

const { createCollector, PROTOCOL, VERSION } = collectorModule;

function fixture(t, html, { interact = true, cursor = false } = {}) {
  const dom = new JSDOM(html, { pretendToBeVisual: true, url: "https://example.test/" });
  const { window } = dom;
  const { document } = window;
  window.HTMLElement.prototype.getBoundingClientRect = function () {
    return { x: 10, y: 10, left: 10, top: 10, right: 110, bottom: 40, width: 100, height: 30 };
  };
  const collector = createCollector(document, { idFactory: () => "document-1" });
  const capabilities = interact ? ["read", "interact"] : ["read"];
  collector.handle({ type: "start", session: "session-1", capabilities, cursor });
  t.after(() => {
    collector.dispose();
    window.close();
  });
  let nextId = 0;
  const send = (operation, payload, generation = null) =>
    collector.handle({
      type: "read-request",
      request: {
        protocol: PROTOCOL,
        version: VERSION,
        type: "request",
        id: `r${++nextId}`,
        session: "session-1",
        tab_id: 7,
        document_id: "document-1",
        capabilities,
        operation,
        payload,
        generation,
      },
    });
  const read = (selector) =>
    send("query", { selector, include_values: true, include_details: true });
  const act = (selector, operation, extra = {}) => {
    const observed = read(selector);
    assert.equal(observed.ok, true);
    document.elementFromPoint = () => document.querySelector(selector);
    return send(
      operation,
      { ref: observed.result.elements.items[0].ref, ...extra },
      observed.result.generation,
    );
  };
  return { window, document, collector, read, send, act };
}

test("read grants cannot be upgraded by starting a second collector session", (t) => {
  const { collector, act } = fixture(t, "<button>Act</button>", { interact: false });
  assert.equal(act("button", "click").error.code, "interaction-not-granted");
  const upgrade = collector.handle({
    type: "start",
    session: "session-2",
    capabilities: ["read", "interact"],
  });
  assert.equal(upgrade.ok, false);
  assert.equal(collector.session, "session-1");
});

test("ordinary fills preserve whitespace and dispatch input/change using the native setter", (t) => {
  const { document, act } = fixture(t, '<input id="entry" value="old">');
  const input = document.querySelector("input");
  const events = [];
  input.addEventListener("input", (event) => events.push([event.type, event.isTrusted]));
  input.addEventListener("change", (event) => events.push([event.type, event.isTrusted]));
  const result = act("input", "fill", { value: "  exact\nvalue  " });
  assert.equal(result.ok, true);
  // A single-line input removes newlines; the receipt must not claim a match.
  assert.equal(result.result.verification, "unverified");
  assert.equal(input.value, "  exactvalue  ");
  assert.deepEqual(events, [
    ["input", false],
    ["change", false],
  ]);
});

test("textarea fill and focus verify their immediate DOM effects", (t) => {
  const { document, act } = fixture(t, '<textarea id="entry">old</textarea>');
  assert.equal(act("textarea", "focus").result.verification, "verified");
  assert.equal(document.activeElement, document.querySelector("textarea"));
  const result = act("textarea", "fill", { value: "  exact\nvalue  " });
  assert.equal(result.result.verification, "verified");
  assert.equal(document.querySelector("textarea").value, "  exact\nvalue  ");
});

test("sensitive labels and autocomplete tokens refuse fills without changing controls", (t) => {
  const { document, act } = fixture(
    t,
    `
    <label for="entry">Security code</label><input id="entry" value="keep">
    <input id="other" autocomplete="section-account shipping cc-number" value="keep">
    <input id="choice" type="checkbox"><input id="upload" type="file">
  `,
  );
  for (const selector of ["#entry", "#other", "#choice"]) {
    const before = document.querySelector(selector).value;
    assert.equal(act(selector, "fill", { value: "replacement" }).ok, false);
    assert.equal(document.querySelector(selector).value, before);
  }
  assert.equal(act("#upload", "click").ok, false);
});

test("programmatic field changes refuse a stale action without overwriting them", (t) => {
  const { document, read, send } = fixture(t, '<input id="entry" value="old">');
  const before = read("input").result;
  document.querySelector("input").value = "changed without an event";
  const result = send(
    "fill",
    { ref: before.elements.items[0].ref, value: "wrong" },
    before.generation,
  );
  assert.equal(result.error.code, "stale-generation");
  assert.equal(document.querySelector("input").value, "changed without an event");
});

test("a covered target is refused before click dispatch", (t) => {
  const { document, read, send } = fixture(t, "<button>Act</button><aside>Cover</aside>");
  let clicks = 0;
  document.querySelector("button").addEventListener("click", () => clicks++);
  const observed = read("button").result;
  document.elementFromPoint = () => document.querySelector("aside");
  assert.equal(
    send("click", { ref: observed.elements.items[0].ref }, observed.generation).error.code,
    "target-covered",
  );
  assert.equal(clicks, 0);
});

test("synthetic clicks do not claim verified application outcomes", (t) => {
  const { document, act } = fixture(t, "<button>Act</button>");
  let clicks = 0;
  document.querySelector("button").addEventListener("click", () => clicks++);
  const result = act("button", "click");
  assert.equal(clicks, 1);
  assert.equal(result.result.verification, "unverified");
});

test("element scroll deltas are applied instead of silently ignored", (t) => {
  const { document, act } = fixture(t, '<div id="pane">Scrollable</div>');
  const pane = document.querySelector("div");
  pane.scrollIntoView = () => assert.fail("delta scrolling must not become scrollIntoView");
  pane.scrollBy = ({ left, top }) => {
    pane.scrollLeft += left;
    pane.scrollTop += top;
  };
  assert.equal(act("div", "scroll", { target: "element", dx: 11, dy: 35 }).ok, true);
  assert.equal(pane.scrollLeft, 11);
  assert.equal(pane.scrollTop, 35);
});

test("waits observe delayed DOM changes and can be explicitly cancelled", async (t) => {
  const { document, send, collector } = fixture(t, "<main></main>");
  const waiting = send("wait", {
    selector: "button",
    state: "present",
    timeout_ms: 500,
    poll_ms: 10,
  });
  setTimeout(() => {
    document.querySelector("main").innerHTML = "<button>Ready</button>";
  }, 20);
  assert.equal((await waiting).result.verification, "verified");
  const cancelled = send("wait", {
    selector: "video",
    state: "present",
    timeout_ms: 500,
    poll_ms: 10,
  });
  collector.handle({ type: "cancel", id: "r2", session: "session-1" });
  assert.equal((await cancelled).error.code, "cancelled");
});

test("secret value conditions do not act as a value oracle; empty ordinary values work", async (t) => {
  const { send } = fixture(
    t,
    '<input id="secret" type="password" value="secret"><input id="entry">',
  );
  const secret = await send("wait", {
    selector: "#secret",
    state: "value",
    value: "secret",
    timeout_ms: 1,
  });
  assert.equal(secret.ok, false);
  const empty = await send("wait", {
    selector: "#entry",
    state: "value",
    value: "",
    timeout_ms: 1,
  });
  assert.equal(empty.ok, true);
});

test("cursor ownership excludes only the indicator; hiding it does not invalidate capture", async (t) => {
  const { collector, document, read, act } = fixture(
    t,
    '<button>Act</button><p data-cu-browser-overlay="true">Page evidence</p>',
    { cursor: true },
  );
  assert.ok(read("p").result.elements.items.length);
  assert.equal(act("button", "focus").ok, true);
  const overlay = collector.overlay;
  assert.equal(overlay.style.width, "36px");
  assert.equal(overlay.style.pointerEvents, "none");
  assert.equal(overlay.style.display, "block");
  const generation = read("button").result.generation;
  const identity = { session: "session-1", document_id: "document-1", generation };
  const prepared = await collector.handle({ type: "capture-prep", ...identity });
  assert.equal(prepared.ok, true);
  assert.equal(overlay.style.display, "none");
  assert.equal(collector.handle({ type: "capture-finish", ...identity }).ok, true);
  assert.equal(overlay.style.display, "block");
  assert.equal(collector.generation, generation);
  document.querySelector("button").textContent = "Changed";
  assert.equal(
    (await collector.handle({ type: "capture-prep", ...identity })).error.code,
    "stale-generation",
  );
  collector.handle({ type: "stop", session: "session-1" });
  assert.equal(overlay.isConnected, false);
});

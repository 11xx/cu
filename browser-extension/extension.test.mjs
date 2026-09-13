import assert from "node:assert/strict";
import { test } from "node:test";
import background from "./background.js";
import collectorModule from "./collector.js";

const { PROTOCOL, VERSION, LIMITS, createController, validateRequest } = background;
const { createCollector } = collectorModule;

function request(operation, overrides = {}) {
  return {
    protocol: PROTOCOL,
    version: VERSION,
    type: "request",
    id: overrides.id || "request-123",
    operation,
    session: overrides.session || "session-1",
    tab_id: overrides.tab_id || 7,
    document_id: overrides.document_id || "document-1",
    generation: overrides.generation === undefined ? null : overrides.generation,
    ...(overrides.capabilities ? { capabilities: overrides.capabilities } : {}),
    payload:
      overrides.payload ||
      (operation === "observe"
        ? { include_values: false, limit: 128 }
        : {
            search: "",
            role: null,
            visible: null,
            page: 0,
            page_size: 100,
            include_values: false,
          }),
  };
}

class FakePort {
  constructor() {
    this.messages = [];
    this.messageListeners = [];
    this.disconnectListeners = [];
    this.onMessage = {
      addListener: (listener) => this.messageListeners.push(listener),
    };
    this.onDisconnect = {
      addListener: (listener) => this.disconnectListeners.push(listener),
    };
    this.disconnected = false;
  }

  postMessage(message) {
    if (this.disconnected) throw new Error("port is disconnected");
    this.messages.push(message);
  }

  disconnect() {
    if (this.disconnected) return;
    this.disconnected = true;
    for (const listener of this.disconnectListeners) listener();
  }

  async emit(message) {
    for (const listener of this.messageListeners) await listener(message);
  }
}

function fakeApi() {
  const updated = [];
  const removed = [];
  const currentTab = { id: 7, windowId: 3, active: true, hidden: false };
  const port = new FakePort();
  const calls = { query: [], execute: [], messages: [], badges: [] };
  const api = {
    runtime: {
      id: "cu-browser-read@local",
      connectNative: () => port,
    },
    windows: { getCurrent: async () => ({ id: 3, focused: true }) },
    scripting: {
      executeScript: async (value) => calls.execute.push(value),
    },
    tabs: {
      query: async (value) => {
        calls.query.push(value);
        return [currentTab];
      },
      get: async () => ({ ...currentTab }),
      sendMessage: async (tabId, message) => {
        calls.messages.push({ tabId, message });
        if (message.type === "start")
          return {
            protocol: PROTOCOL,
            version: VERSION,
            type: "started",
            ok: true,
            session: message.session,
            capabilities: message.capabilities,
            document_id: "document-1",
            generation: 0,
          };
        if (message.type === "read-request") {
          const result =
            message.request.operation === "select"
              ? {
                  kind: "action",
                  action: "select",
                  ref: message.request.payload.ref,
                  backend: "dom",
                  verification: "verified",
                  generation: 1,
                  document_id: "document-1",
                  requested_option: { by: "value", value: "two" },
                  selection: { value: "two", label: "Two" },
                }
              : {
                  kind: "observation",
                  generation: 0,
                  document_id: "document-1",
                };
          return {
            protocol: PROTOCOL,
            version: VERSION,
            type: "response",
            id: message.request.id,
            session: message.request.session,
            tab_id: tabId,
            document_id: "document-1",
            ok: true,
            result,
          };
        }
        return {
          protocol: PROTOCOL,
          version: VERSION,
          type: "stopped",
          ok: true,
        };
      },
      onUpdated: { addListener: (listener) => updated.push(listener) },
      onRemoved: { addListener: (listener) => removed.push(listener) },
    },
    browserAction: {
      onClicked: { addListener: () => {} },
      setBadgeText: async (value) => calls.badges.push(value),
      setBadgeBackgroundColor: async () => {},
    },
  };
  return { api, currentTab, port, updated, removed, calls };
}

test("installation starts with no grant and only the current tab can be granted", async () => {
  const { api, port, calls } = fakeApi();
  const controller = createController(api);
  assert.equal(controller.status().active, false);
  const started = await controller.startCurrent();
  assert.equal(started.ok, true);
  assert.equal(started.grant.tab_id, 7);
  assert.deepEqual(calls.query, [{ active: true, currentWindow: true }]);
  assert.equal(calls.execute[0].target.tabId, 7);
  assert.equal(port.messages[0].type, "hello");
  assert.equal(port.messages[0].grants[0].tab_id, 7);

  await controller.handleTabUpdated(8, { status: "loading" });
  assert.equal(controller.status().active, true);
  await controller.handleTabUpdated(7, { status: "loading" });
  assert.equal(controller.status().active, false);
  assert.equal(port.messages.at(-1).type, "revoke");
  assert.equal(port.disconnected, true);
});

test("native requests reject arbitrary tabs and action-like operations", async () => {
  const { api, port } = fakeApi();
  const controller = createController(api);
  const started = await controller.startCurrent();
  await port.emit({ ...request("click", { id: "bad-operation" }) });
  assert.equal(port.messages.at(-1).ok, false);
  assert.equal(port.messages.at(-1).error.code, "invalid-request");
  await port.emit(
    request("select", {
      id: "read-only-select",
      session: started.grant.session,
      generation: 0,
      payload: { ref: "e1", value: "two" },
    }),
  );
  assert.equal(port.messages.at(-1).ok, false);
  assert.equal(port.messages.at(-1).error.code, "interaction-not-granted");

  await port.emit({ ...request("observe", { id: "wrong-tab", tab_id: 99 }) });
  assert.equal(port.messages.at(-1).error.code, "tab-not-granted");
  assert.throws(() => validateRequest({ ...request("click") }), /passive read/);
});

test("attribute mutations invalidate an observed action generation", () => {
  const oldObserver = globalThis.MutationObserver;
  let callback;
  globalThis.MutationObserver = class {
    constructor(fn) {
      callback = fn;
    }
    observe() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  };
  try {
    const button = new Element("button", { text: "Act" });
    const collector = createCollector(new Document([button]), { idFactory: () => "document-1" });
    collector.handle({ type: "start", session: "session-1", capabilities: ["read", "interact"] });
    const read = collector.handle({ type: "read-request", request: request("observe") });
    callback([{ type: "attributes", target: button, addedNodes: [], removedNodes: [] }]);
    assert.ok(collector.generation > read.result.generation);
  } finally {
    globalThis.MutationObserver = oldObserver;
  }
});

test("background reads report current tab activity, not activation-time metadata", async () => {
  const { api, currentTab, port } = fakeApi();
  const controller = createController(api);
  const started = await controller.startCurrent();
  currentTab.active = false;
  await controller.handleNativeMessage(
    request("observe", { session: started.grant.session }),
    port,
  );
  assert.equal(port.messages.at(-1).result.context.tab_active, false);
});

test("background forwards select results with explicit grant provenance", async () => {
  const { api, port } = fakeApi();
  const controller = createController(api);
  const started = await controller.startCurrent(null, ["read", "interact"]);
  await controller.handleNativeMessage(
    request("select", {
      session: started.grant.session,
      generation: 0,
      payload: { ref: "e1", value: "two" },
    }),
    port,
  );
  const response = port.messages.at(-1);
  assert.equal(response.ok, true);
  assert.deepEqual(response.result.selection, { value: "two", label: "Two" });
  assert.deepEqual(response.result.grant_provenance, {
    state: "explicit",
    authority: "interaction-grant",
    capabilities: ["read", "interact"],
    session: started.grant.session,
    tab_id: 7,
    window_id: 3,
    document_id: "document-1",
    generation: 1,
  });
});

class Element {
  constructor(
    tag,
    {
      text = "",
      attrs = {},
      value,
      rect = { x: 1, y: 2, width: 10, height: 10 },
      children = [],
    } = {},
  ) {
    this.localName = tag;
    this.tagName = tag.toUpperCase();
    this.attrs = new Map(Object.entries(attrs));
    this._text = text;
    this.value = value;
    this.rect = rect;
    this.children = children;
    this.hidden = false;
    this.multiple = false;
    for (const child of children) child.parentElement = this;
  }

  get childNodes() {
    return [...(this._text ? [{ nodeType: 3, data: this._text }] : []), ...this.children];
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join(" ");
  }

  get innerText() {
    return this.textContent;
  }

  getAttribute(name) {
    return this.attrs.has(name) ? this.attrs.get(name) : null;
  }

  getBoundingClientRect() {
    return this.rect;
  }

  closest() {
    return null;
  }

  querySelectorAll() {
    const result = [];
    const visit = (node) => {
      for (const child of node.children) {
        result.push(child);
        visit(child);
      }
    };
    visit(this);
    return result;
  }
}

class Document {
  constructor(children) {
    this.body = new Element("body", { children });
    this.documentElement = this.body;
    this.visibilityState = "visible";
    this.listeners = new Map();
    this.defaultView = {
      listeners: new Map(),
      addEventListener: (event, listener) => this.defaultView.listeners.set(event, listener),
      removeEventListener: (event) => this.defaultView.listeners.delete(event),
      getComputedStyle: (element) => ({
        display: element.getAttribute("data-display") || "",
        visibility: element.getAttribute("data-visibility") || "",
      }),
    };
  }

  createTreeWalker(root) {
    const nodes = root.querySelectorAll("*");
    let index = 0;
    return { nextNode: () => nodes[index++] || null };
  }

  addEventListener(event, listener) {
    this.listeners.set(event, listener);
  }

  removeEventListener(event) {
    this.listeners.delete(event);
  }

  getElementById(id) {
    return (
      this.body.querySelectorAll("*").find((element) => element.getAttribute("id") === id) || null
    );
  }

  dispatch(event) {
    this.listeners.get(event)?.();
    this.defaultView.listeners.get(event)?.();
  }
}

test("collector bounds queries, redacts fields, and labels freshness", () => {
  const email = new Element("input", {
    attrs: { type: "email", name: "email" },
    value: "person@example.test",
  });
  const password = new Element("input", {
    attrs: { type: "password", name: "password" },
    value: "secret",
  });
  const launch = new Element("button", { text: "Launch" });
  const doc = new Document([password, email, launch]);
  const collector = createCollector(doc, { idFactory: () => "document-1" });
  const started = collector.handle({ type: "start", session: "session-1" });
  assert.equal(started.document_id, "document-1");

  const observed = collector.handle({
    type: "read-request",
    request: request("observe", {
      payload: { include_values: true, limit: 2 },
    }),
  });
  assert.equal(observed.ok, true);
  assert.equal(observed.result.elements.truncated, true);
  const fields = observed.result.elements.items.filter((item) => item.tag === "input");
  assert.equal(fields.find((item) => item.value.state === "redacted").value.state, "redacted");
  assert.equal(observed.result.values.state, "available");
  assert.equal(observed.result.limitations.css_projection.state, "unsupported");

  const page = collector.handle({
    type: "read-request",
    request: request("query", {
      payload: {
        search: "launch",
        role: "button",
        visible: true,
        page: 0,
        page_size: 1,
        include_values: false,
      },
    }),
  });
  assert.equal(page.result.elements.items[0].accessible_name.value, "Launch");
  assert.equal(page.result.pagination.state, "available");
  const emptyPage = collector.handle({
    type: "read-request",
    request: request("query", {
      payload: {
        search: "launch",
        role: "button",
        visible: true,
        page: 1,
        page_size: 1,
        include_values: false,
      },
    }),
  });
  assert.equal(emptyPage.result.elements.state, "empty");
  const overbound = collector.handle({
    type: "read-request",
    request: request("query", {
      payload: {
        search: "x".repeat(257),
        role: null,
        visible: null,
        page: 0,
        page_size: 1,
        include_values: false,
      },
    }),
  });
  assert.equal(overbound.ok, false);
  assert.equal(overbound.error.code, "invalid-request");

  const oldGeneration = collector.generation;
  doc.dispatch("input");
  assert.equal(collector.generation, oldGeneration + 1);
  const stale = collector.handle({
    type: "read-request",
    request: request("observe", { generation: oldGeneration }),
  });
  assert.equal(stale.ok, false);
  assert.equal(stale.error.code, "stale-generation");
  const current = collector.handle({
    type: "read-request",
    request: request("observe", { generation: collector.generation }),
  });
  assert.equal(current.ok, true);
  collector.dispose();
});

test("document identities are distinct and unknown collector operations are refused", () => {
  const first = createCollector(new Document([]), {
    idFactory: () => "document-a",
  });
  const second = createCollector(new Document([]), {
    idFactory: () => "document-b",
  });
  assert.notEqual(first.documentId, second.documentId);
  assert.equal(first.handle({ type: "start", session: "session-1" }).document_id, "document-a");
  const refused = first.handle({ type: "click" });
  assert.equal(refused.ok, false);
  assert.equal(refused.error.code, "invalid-message");
  first.dispose();
  second.dispose();
});

test("stop during asynchronous activation cannot grant a tab afterwards", async () => {
  const { api } = fakeApi();
  let release;
  api.scripting.executeScript = () =>
    new Promise((resolve) => {
      release = resolve;
    });
  const controller = createController(api);
  const starting = controller.start({ id: 7, windowId: 3, active: true });
  await controller.stop();
  release();
  await starting;
  assert.equal(controller.status().active, false);
});

test("queries can find matches beyond the initial observation page", () => {
  const doc = new Document([
    ...Array.from({ length: 150 }, () => new Element("button", { text: "Other" })),
    new Element("button", { text: "Needle" }),
  ]);
  const collector = createCollector(doc);
  const result = collector.query({
    search: "needle",
    role: "button",
    visible: true,
    page: 0,
    page_size: 10,
    include_values: false,
  });
  assert.equal(result.elements.items.length, 1);
});

test("default text cannot leak textarea values through ancestor text", () => {
  const secret = new Element("textarea", {
    text: "SENSITIVE_FIXTURE",
    attrs: { name: "secret" },
  });
  const collector = createCollector(new Document([new Element("div", { children: [secret] })]));
  const result = collector.observe({ include_values: false, limit: 128 });
  assert.equal(JSON.stringify(result).includes("SENSITIVE_FIXTURE"), false);
});

test("Firefox message listener returns a Promise, not an ignored plain object", async () => {
  let listener;
  collectorModule.install(
    {
      runtime: {
        onMessage: {
          addListener(fn) {
            listener = fn;
          },
        },
      },
    },
    new Document([]),
  );
  const reply = listener({ type: "start", session: "fixture-session" });
  assert.equal(typeof reply.then, "function");
  assert.equal((await reply).ok, true);
});

test("repeated scope detects programmatic values and preserves whitespace", () => {
  const input = new Element("input", { value: "  old\\nvalue  " });
  const collector = createCollector(new Document([input]), {
    idFactory: () => "document-1",
  });
  collector.handle({ type: "start", session: "session-1" });
  const message = {
    type: "read-request",
    request: request("observe", {
      payload: { include_values: true, limit: 128 },
    }),
  };
  const before = collector.handle(message);
  assert.equal(before.result.elements.items[0].value.value, "  old\\nvalue  ");
  input.value = "new value";
  message.request.generation = before.result.generation;
  assert.equal(collector.handle(message).error.code, "stale-generation");
});

assert.equal(LIMITS.max_message_bytes, 1024 * 1024);

test("read-only grants refuse action requests and refs remain stable", () => {
  const button = new Element("button", { text: "Run" });
  const doc = new Document([button]);
  const collector = createCollector(doc, { idFactory: () => "document-1" });
  collector.handle({ type: "start", session: "session-1" });
  const observed = collector.handle({
    type: "read-request",
    request: request("observe", {
      payload: { include_values: false, limit: 10 },
    }),
  });
  const ref = observed.result.elements.items[0].ref;
  assert.equal(
    collector.query({
      search: "run",
      role: "button",
      visible: true,
      page: 0,
      page_size: 10,
      include_values: false,
    }).elements.items[0].ref,
    ref,
  );
  const refused = collector.handle({
    type: "read-request",
    request: request("click", {
      generation: observed.result.generation,
      payload: { ref },
    }),
  });
  assert.equal(refused.error.code, "interaction-not-granted");
  collector.dispose();
});

test("interaction actions reject a detached ref and preserve capability boundaries", () => {
  const button = new Element("button", { text: "Run" });
  const doc = new Document([button]);
  const collector = createCollector(doc, { idFactory: () => "document-1" });
  collector.handle({
    type: "start",
    session: "session-1",
    capabilities: ["read", "interact"],
  });
  const observed = collector.handle({
    type: "read-request",
    request: request("observe", {
      payload: { include_values: false, limit: 10 },
    }),
  });
  button.parentElement = null;
  const refused = collector.handle({
    type: "read-request",
    request: request("click", {
      generation: observed.result.generation,
      capabilities: ["read", "interact"],
      payload: { ref: observed.result.elements.items[0].ref },
    }),
  });
  assert.equal(refused.error.code, "stale-element");
  collector.dispose();
});

test("media reads and bounded card details label text metadata honestly", () => {
  const link = new Element("a", {
    text: "Story",
    attrs: { href: "https://example.test/story" },
  });
  const card = new Element("div", {
    text: "Story 1:23",
    attrs: { class: "card" },
    children: [link],
  });
  const video = new Element("video", {
    rect: { x: 1, y: 2, width: 10, height: 10 },
  });
  video.duration = 120;
  video.currentTime = 12;
  video.paused = false;
  video.ended = false;
  video.readyState = 4;
  const doc = new Document([card, video]);
  const collector = createCollector(doc, { idFactory: () => "document-1" });
  const result = collector.observe({
    include_values: false,
    include_details: true,
    limit: 10,
  });
  assert.equal(result.items, undefined);
  const cardItem = result.elements.items.find((item) => item.tag === "div");
  assert.equal(cardItem.links.items[0].href, "https://example.test/story");
  assert.equal(cardItem.time_candidates.items[0].proven, false);
  assert.equal(result.elements.items.find((item) => item.tag === "video").media.duration, 120);
  collector.dispose();
});

test("wait resolves a delayed selector without replaying an action", async () => {
  const card = new Element("div", { attrs: { class: "card" } });
  const base = new Document([card]);
  base.querySelectorAll = (selector) =>
    selector === ".card" ? [card] : base.body.querySelectorAll(selector);
  const collector = createCollector(base, { idFactory: () => "document-1" });
  collector.handle({ type: "start", session: "session-1" });
  const pending = collector.handle({
    type: "read-request",
    request: request("wait", {
      payload: {
        selector: ".card",
        state: "text",
        text: "Ready",
        timeout_ms: 200,
        poll_ms: 10,
      },
    }),
  });
  setTimeout(() => {
    card._text = "Ready";
  }, 20);
  const result = await pending;
  assert.equal(result.ok, true);
  assert.equal(result.result.verification, "verified");
  collector.dispose();
});

test("capture brackets collector identity and rejects an unprepared document", async () => {
  const { api, port } = fakeApi();
  const original = api.tabs.sendMessage;
  let captures = 0;
  let preparationAllowed = true;
  let activation;
  api.tabs.onActivated = {
    addListener: (fn) => {
      activation = fn;
    },
  };
  let switchDuringCapture = false;
  api.tabs.captureVisibleTab = async () => {
    captures++;
    if (switchDuringCapture) {
      activation();
      activation();
    }
    return "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAADklEQVR4nGP4z8DwH4QBEfcD/ePF9e8AAAAASUVORK5CYII=";
  };
  api.tabs.sendMessage = async (tab, message) => {
    if (message.type.startsWith("capture-")) {
      if (!preparationAllowed) return { ok: false, error: { code: "stale-generation" } };
      assert.equal(message.document_id, "document-1");
      assert.equal(message.generation, 0);
      return {
        ok: true,
        document_id: "document-1",
        generation: 0,
        viewport: { width: 800, height: 600, space: "css-viewport" },
      };
    }
    return original(tab, message);
  };
  const controller = createController(api);
  const started = await controller.startCurrent(null, ["read", "interact"]);
  const capture = request("capture", {
    session: started.grant.session,
    generation: 0,
    payload: {},
  });
  await controller.handleNativeMessage(capture, port);
  assert.equal(port.messages.at(-1).ok, true, JSON.stringify(port.messages.at(-1)));
  assert.deepEqual(port.messages.at(-1).result.pixels, {
    width: 2,
    height: 1,
    space: "image-pixels",
  });
  assert.equal(port.messages.at(-1).result.verification, "identity-bracketed");
  preparationAllowed = false;
  await controller.handleNativeMessage(capture, port);
  assert.equal(port.messages.at(-1).error.code, "stale-generation");
  assert.equal(captures, 1);
  preparationAllowed = true;
  switchDuringCapture = true;
  await controller.handleNativeMessage(capture, port);
  assert.equal(port.messages.at(-1).ok, false);
  assert.equal(captures, 2);
});

test("region capture validates CSS geometry, image scale, identity, and budget", async () => {
  const { api, port } = fakeApi();
  const original = api.tabs.sendMessage;
  const pattern =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR4nGP4z8DwHwyBNBAw/AcAR8oI+ItOQ4UAAAAASUVORK5CYII=";
  let mode = "valid";
  api.tabs.captureVisibleTab = async () => pattern;
  api.tabs.sendMessage = async (tab, message) => {
    if (!message.type.startsWith("capture-")) return original(tab, message);
    if (mode === "identity") return { ok: true, document_id: "other-document", generation: 0 };
    if (mode === "geometry") return { ok: false, error: { code: "unsupported-geometry" } };
    return {
      ok: true,
      document_id: "document-1",
      generation: 0,
      viewport:
        mode === "scale"
          ? { width: 4, height: 2, space: "css-viewport" }
          : { width: 2, height: 2, space: "css-viewport" },
    };
  };
  const controller = createController(api);
  const started = await controller.startCurrent(null, ["read", "interact"]);
  const region = { x: 1, y: 0, width: 1, height: 1, space: "css-viewport" };
  await controller.handleNativeMessage(
    request("capture", {
      id: "region-valid",
      session: started.grant.session,
      generation: 0,
      payload: { region },
    }),
    port,
  );
  assert.equal(port.messages.at(-1).ok, true);
  assert.deepEqual(port.messages.at(-1).result.requested_coverage, region);
  assert.deepEqual(port.messages.at(-1).result.requested_image_pixels, {
    x: 1,
    y: 0,
    width: 1,
    height: 1,
  });
  assert.deepEqual(port.messages.at(-1).result.capture_scale, {
    x: 1,
    y: 1,
    space: "css-viewport-to-image-pixels",
  });

  mode = "scale";
  await controller.handleNativeMessage(
    request("capture", {
      id: "region-scale",
      session: started.grant.session,
      generation: 0,
      payload: { region },
    }),
    port,
  );
  assert.equal(port.messages.at(-1).error.code, "capture-scale-mismatch");

  mode = "identity";
  await controller.handleNativeMessage(
    request("capture", {
      id: "region-identity",
      session: started.grant.session,
      generation: 0,
      payload: { region },
    }),
    port,
  );
  assert.equal(port.messages.at(-1).error.code, "stale-document");

  mode = "geometry";
  await controller.handleNativeMessage(
    request("capture", {
      id: "region-geometry",
      session: started.grant.session,
      generation: 0,
      payload: { region },
    }),
    port,
  );
  assert.equal(port.messages.at(-1).error.code, "unsupported-geometry");

  mode = "valid";
  api.tabs.captureVisibleTab = async () => `data:image/png;base64,${"A".repeat(700000)}`;
  await controller.handleNativeMessage(
    request("capture", {
      id: "region-budget",
      session: started.grant.session,
      generation: 0,
      payload: { region },
    }),
    port,
  );
  assert.equal(port.messages.at(-1).error.code, "observation-too-large");
});

test("late collector cleanup cannot disconnect a subsequent grant", async () => {
  const { api, port } = fakeApi();
  const original = api.tabs.sendMessage;
  let release;
  const cleanup = new Promise((resolve) => {
    release = resolve;
  });
  api.tabs.sendMessage = (tab, message) =>
    message.type === "stop" ? cleanup : original(tab, message);
  const controller = createController(api);
  await controller.startCurrent();
  const stopping = controller.stop();
  const nextPort = new FakePort();
  api.runtime.connectNative = () => nextPort;
  const next = await controller.startCurrent();
  release({ ok: true });
  await stopping;
  assert.equal(next.ok, true);
  assert.equal(port.disconnected, true);
  assert.notEqual(controller.status().native, "unavailable");
  await controller.handleNativeMessage(
    request("observe", { session: next.grant.session }),
    nextPort,
  );
  assert.equal(nextPort.messages.at(-1).ok, true);
});

test("a tab moved to another browser window cannot retain action authority", async () => {
  const { api, currentTab, port, calls } = fakeApi();
  const controller = createController(api);
  const started = await controller.startCurrent(null, ["read", "interact"]);
  currentTab.windowId = 4;
  api.windows.getCurrent = async () => ({ id: 4, focused: true });
  await controller.handleNativeMessage(
    request("click", {
      session: started.grant.session,
      generation: 0,
      payload: { ref: "e1" },
    }),
    port,
  );
  assert.equal(calls.messages.filter((call) => call.message.type === "read-request").length, 0);
});

test("cancellation prevents an action waiting on focus checks from being dispatched", async () => {
  const { api, currentTab, port, calls } = fakeApi();
  const controller = createController(api);
  const started = await controller.startCurrent(null, ["read", "interact"]);
  let release;
  const blocked = new Promise((resolve) => {
    release = resolve;
  });
  let focusChecks = 0;
  api.tabs.query = async () => {
    focusChecks++;
    await blocked;
    return [currentTab];
  };
  const action = request("click", {
    session: started.grant.session,
    generation: 0,
    payload: { ref: "e1" },
  });
  const pending = controller.handleNativeMessage(action, port);
  await Promise.resolve();
  assert.equal(focusChecks, 1);
  await controller.handleNativeMessage(
    {
      protocol: PROTOCOL,
      version: VERSION,
      type: "cancel",
      id: action.id,
      session: action.session,
    },
    port,
  );
  release();
  await pending;
  assert.equal(calls.messages.filter((call) => call.message.type === "read-request").length, 0);
});

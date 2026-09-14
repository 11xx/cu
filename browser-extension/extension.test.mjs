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
  const calls = { query: [], execute: [], messages: [], badges: [], reloads: [], order: [] };
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
        if (message.type === "stop") calls.order.push("stop");
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
      reload: async (tabId) => {
        calls.order.push("reload");
        calls.reloads.push(tabId);
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

test("installation starts with no grant and a navigation revokes without closing the host", async () => {
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
  assert.equal(port.disconnected, false);
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
  const next = await controller.startCurrent();
  release({ ok: true });
  await stopping;
  assert.equal(next.ok, true);
  assert.equal(port.disconnected, false);
  assert.notEqual(controller.status().native, "unavailable");
  await controller.handleNativeMessage(request("observe", { session: next.grant.session }), port);
  assert.equal(port.messages.at(-1).ok, true);
});

test("a lost native host revokes the grant and stays reported as unavailable", async () => {
  const { api, port } = fakeApi();
  const controller = createController(api);
  const started = await controller.startCurrent(null, ["read", "interact"]);
  assert.equal(started.ok, true);
  port.disconnect();
  await new Promise((resolve) => setImmediate(resolve));
  const status = controller.status();
  assert.equal(status.active, false);
  assert.equal(status.native_error, "native-unavailable");
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

function reloadRequest(session, overrides = {}) {
  return request("reload", { session, generation: 0, payload: {}, ...overrides });
}

test("reload revokes the grant, dispatches once, and confers nothing on the new document", async () => {
  const { api, port, calls } = fakeApi();
  const controller = createController(api);
  const started = await controller.startCurrent(null, ["read", "interact"]);
  await controller.handleNativeMessage(reloadRequest(started.grant.session), port);

  assert.deepEqual(calls.reloads, [7]);
  assert.deepEqual(calls.order, ["stop", "reload"]);
  const revokeIndex = port.messages.findIndex((message) => message.type === "revoke");
  const response = port.messages.at(-1);
  assert.ok(revokeIndex >= 0 && revokeIndex < port.messages.length - 1);
  assert.equal(port.messages[revokeIndex].reason, "reload");
  assert.equal(response.ok, true, JSON.stringify(response));
  assert.equal(response.result.backend, "tabs");
  assert.equal(response.result.effect, "not-observed");
  assert.equal(response.result.new_document_grant, "none");
  assert.equal(response.result.revoked_grant.document_id, "document-1");
  assert.equal(port.disconnected, false);
  assert.equal(controller.status().active, false);

  await controller.handleNativeMessage(
    request("click", { session: started.grant.session, generation: 0, payload: { ref: "e1" } }),
    port,
  );
  assert.equal(calls.messages.filter((call) => call.message.type === "read-request").length, 0);
  assert.deepEqual(calls.reloads, [7]);

  const next = await controller.startCurrent(null, ["read", "interact"]);
  assert.equal(next.ok, true);
  assert.notEqual(next.grant.session, started.grant.session);
  assert.equal(controller.status().grant.session, next.grant.session);
});

test("refused reloads dispatch nothing and leave the grant in place", async () => {
  const cases = [
    { name: "read-only", capabilities: ["read"], code: "interaction-not-granted" },
    { name: "stale generation", overrides: { generation: 5 }, code: "stale-generation" },
    { name: "wrong tab", overrides: { tab_id: 99 }, code: "tab-not-granted" },
    { name: "stale document", overrides: { document_id: "document-old" }, code: "stale-document" },
    { name: "inactive tab", inactive: true, code: "not-current-tab" },
    { name: "no reload API", noReload: true, code: "reload-unavailable" },
  ];
  for (const scenario of cases) {
    const { api, currentTab, port, calls } = fakeApi();
    const controller = createController(api);
    const started = await controller.startCurrent(
      null,
      scenario.capabilities || ["read", "interact"],
    );
    if (scenario.inactive) currentTab.active = false;
    if (scenario.noReload) delete api.tabs.reload;
    await controller.handleNativeMessage(
      reloadRequest(started.grant.session, scenario.overrides),
      port,
    );
    assert.equal(port.messages.at(-1).error?.code, scenario.code, scenario.name);
    assert.deepEqual(calls.reloads, [], scenario.name);
    assert.equal(controller.status().active, true, scenario.name);
    assert.equal(port.disconnected, false, scenario.name);
  }
  assert.throws(
    () => validateRequest(request("reload", { generation: 0, payload: { ref: "e1" } })),
    /unsupported message field ref/,
  );
  assert.throws(() => validateRequest(request("reload", { payload: {} })), /passive read/);
});

test("requests queued behind a reload are refused as revoked and never run", async () => {
  const { api, port, calls } = fakeApi();
  const controller = createController(api);
  const started = await controller.startCurrent(null, ["read", "interact"]);
  const session = started.grant.session;
  await Promise.all([
    controller.handleNativeMessage(reloadRequest(session, { id: "reload-1" }), port),
    controller.handleNativeMessage(
      request("click", { id: "click-after", session, generation: 0, payload: { ref: "e1" } }),
      port,
    ),
  ]);
  const refused = port.messages.find((message) => message.id === "click-after");
  assert.equal(refused.error.code, "grant-revoked");
  assert.equal(refused.error.state, "grant-revoked");
  assert.equal(calls.messages.filter((call) => call.message.type === "read-request").length, 0);
  assert.deepEqual(calls.reloads, [7]);
});

test("stop during reload preparation dispatches nothing and no grant shares the closing port", async () => {
  const { api, port, calls } = fakeApi();
  const original = api.tabs.sendMessage;
  let release;
  const stopping = new Promise((resolve) => {
    release = resolve;
  });
  let stopSeen;
  const stopRequested = new Promise((resolve) => {
    stopSeen = resolve;
  });
  api.tabs.sendMessage = (tab, message) => {
    if (message.type !== "stop") return original(tab, message);
    stopSeen();
    return stopping;
  };
  const controller = createController(api);
  const started = await controller.startCurrent(null, ["read", "interact"]);
  const reloading = controller.handleNativeMessage(reloadRequest(started.grant.session), port);
  await stopRequested;
  const refused = await controller.startCurrent(null, ["read", "interact"]);
  assert.equal(refused.error.code, "session-active");
  await controller.stop();
  release({ ok: true });
  await reloading;

  assert.deepEqual(calls.reloads, []);
  assert.equal(port.disconnected, false);
  assert.equal(
    port.messages.some((message) => message.type === "response" && message.ok),
    false,
  );
  const next = await controller.startCurrent(null, ["read", "interact"]);
  assert.equal(next.ok, true);
});

test("a rejected reload or a tab lost before dispatch keeps the grant revoked", async () => {
  {
    const { api, port } = fakeApi();
    api.tabs.reload = async () => {
      throw new Error("no such tab");
    };
    const controller = createController(api);
    const started = await controller.startCurrent(null, ["read", "interact"]);
    await controller.handleNativeMessage(reloadRequest(started.grant.session), port);
    const response = port.messages.at(-1);
    assert.equal(response.error.code, "reload-rejected");
    assert.equal(response.error.state, "grant-revoked");
    assert.equal(controller.status().active, false);
    assert.equal(port.disconnected, false);
  }
  {
    const { api, currentTab, port, calls } = fakeApi();
    const controller = createController(api);
    const started = await controller.startCurrent(null, ["read", "interact"]);
    let checks = 0;
    api.tabs.query = async () => (++checks === 1 ? [currentTab] : []);
    await controller.handleNativeMessage(reloadRequest(started.grant.session), port);
    assert.equal(port.messages.at(-1).error.code, "not-current-tab");
    assert.equal(port.messages.at(-1).error.state, "grant-revoked");
    assert.deepEqual(calls.reloads, []);
    assert.equal(controller.status().active, false);
  }
});

test("a callback-mode install connects its host and grants nothing from the popup", async () => {
  const { api, calls } = fakeApi();
  let listener;
  let connections = 0;
  const connect = api.runtime.connectNative;
  api.runtime.connectNative = () => (connections++, connect());
  api.runtime.onMessage = { addListener: (fn) => (listener = fn) };
  api.tabs.onUpdated = { addListener: () => {} };
  api.tabs.onRemoved = { addListener: () => {} };
  api.action = api.browserAction;
  delete api.browserAction;
  background.install(api, {
    respond: "callback",
    collectorFiles: ["collector.js", "chromium-collector.js"],
  });
  assert.equal(connections, 1);
  const sender = { id: api.runtime.id };
  const send = (message) => new Promise((resolve) => listener(message, sender, resolve));
  for (const type of ["start-read", "start-interaction", "stop"]) {
    const reply = await send({ type });
    assert.equal(reply.error.code, "invalid-ui-message", type);
  }
  assert.equal((await send({ type: "status" })).active, false);
  assert.deepEqual(calls.execute, []);
});

test("the default install keeps returning the reply Promise", async () => {
  const { api } = fakeApi();
  let listener;
  api.runtime.onMessage = { addListener: (fn) => (listener = fn) };
  api.tabs.onUpdated = { addListener: () => {} };
  api.tabs.onRemoved = { addListener: () => {} };
  background.install(api);
  const reply = listener({ type: "status" }, { id: api.runtime.id }, () => {
    throw new Error("sendResponse must not be used in the default mode");
  });
  assert.equal(typeof reply.then, "function");
  assert.equal((await reply).active, false);
});

function readyMessage() {
  return {
    protocol: PROTOCOL,
    version: VERSION,
    type: "ready",
    helper_id: "helper-test",
    socket: "/run/user/1000/cu/browser/helper-test.sock",
    limits: { ...LIMITS },
  };
}

function control(operation, overrides = {}) {
  return {
    protocol: PROTOCOL,
    version: VERSION,
    type: "control",
    id: overrides.id || "control-1",
    operation,
    tab_id: overrides.tab_id === undefined ? null : overrides.tab_id,
    ...(overrides.capabilities ? { capabilities: overrides.capabilities } : {}),
    ...(overrides.cursor === undefined ? {} : { cursor: overrides.cursor }),
  };
}

test("a session control binds a named background tab and announces the grant first", async () => {
  const { api, currentTab, port, calls } = fakeApi();
  currentTab.active = false;
  const controller = createController(api);
  controller.connectNative();
  await controller.handleNativeMessage(readyMessage(), port);
  await controller.handleNativeMessage(
    control("grant", { tab_id: 7, capabilities: ["read", "interact"], cursor: false }),
    port,
  );
  const grantIndex = port.messages.findIndex((message) => message.type === "grant");
  const response = port.messages.at(-1);
  assert.ok(grantIndex >= 0 && grantIndex < port.messages.length - 1);
  assert.equal(response.ok, true, JSON.stringify(response));
  assert.equal(response.id, "control-1");
  assert.equal(response.tab_id, 7);
  assert.equal(response.session, response.result.grant.session);
  assert.deepEqual(response.result.grant.capabilities, ["read", "interact"]);
  assert.equal(calls.execute[0].target.tabId, 7);
  assert.equal(calls.messages.find((call) => call.message.type === "start").message.cursor, false);
  assert.deepEqual(calls.query, []);
});

test("a release control revokes the grant, keeps the host, and refuses a second release", async () => {
  const { api, port } = fakeApi();
  const controller = createController(api);
  controller.connectNative();
  await controller.handleNativeMessage(readyMessage(), port);
  await controller.handleNativeMessage(control("grant"), port);
  const granted = port.messages.at(-1).result.grant;
  await controller.handleNativeMessage(control("release", { id: "control-2" }), port);
  const released = port.messages.at(-1);
  assert.equal(released.ok, true, JSON.stringify(released));
  assert.equal(released.result.revoked_grant.session, granted.session);
  assert.equal(port.messages.at(-2).type, "revoke");
  assert.equal(port.messages.at(-2).reason, "client-stop");
  assert.equal(controller.status().active, false);
  assert.equal(port.disconnected, false);
  await controller.handleNativeMessage(control("release", { id: "control-3" }), port);
  assert.equal(port.messages.at(-1).error.code, "tab-not-granted");
});

test("malformed session controls are refused before any tab is touched", async () => {
  const { api, port, calls } = fakeApi();
  const controller = createController(api);
  controller.connectNative();
  for (const [name, message] of [
    ["unknown operation", control("transfer")],
    ["release with capabilities", control("release", { capabilities: ["read"] })],
    ["fractional tab", control("grant", { tab_id: 1.5 })],
    ["older protocol", { ...control("grant"), version: VERSION - 1 }],
  ]) {
    await controller.handleNativeMessage(message, port);
    assert.equal(port.messages.at(-1).error.code, "invalid-request", name);
  }
  assert.deepEqual(calls.execute, []);
});

test("a grant that names no cursor uses the stored popup setting", async () => {
  const { api, port, calls } = fakeApi();
  api.storage = { local: { get: async (defaults) => ({ ...defaults, cursor: false }) } };
  const controller = createController(api);
  controller.connectNative();
  await controller.handleNativeMessage(control("grant"), port);
  assert.equal(port.messages.at(-1).ok, true, JSON.stringify(port.messages.at(-1)));
  assert.equal(calls.messages.find((call) => call.message.type === "start").message.cursor, false);
});

test("a lost native host is retried no sooner than the retry interval", () => {
  const { api } = fakeApi();
  const ports = [];
  api.runtime.connectNative = () => {
    const next = new FakePort();
    ports.push(next);
    return next;
  };
  const controller = createController(api);
  controller.connectNative();
  controller.connectNative();
  assert.equal(ports.length, 1);
  ports[0].disconnect();
  controller.connectNative();
  assert.equal(ports.length, 1);
  assert.equal(controller.status().native_error, "native-unavailable");
  const now = Date.now;
  Date.now = () => now() + 6000;
  try {
    controller.connectNative();
  } finally {
    Date.now = now;
  }
  assert.equal(ports.length, 2);
  assert.equal(ports[1].messages[0].type, "hello");
});

function debuggerFake(api, responses = {}) {
  const calls = [];
  api.debugger = {
    attach: async (target, version) => calls.push(["attach", target.tabId, version]),
    detach: async (target) => calls.push(["detach", target.tabId]),
    sendCommand: async (target, method, params) => {
      calls.push(["send", method, params]);
      return responses[method] || {};
    },
  };
  return calls;
}

async function grantWithDebugger(controller, port, id = "debug") {
  await controller.handleNativeMessage(
    control("grant", { id: `grant-${id}`, capabilities: ["read", "interact"] }),
    port,
  );
  const session = port.messages.at(-1).result.grant.session;
  await controller.handleNativeMessage(control("debug-attach", { id: `attach-${id}` }), port);
  assert.equal(port.messages.at(-1).ok, true, JSON.stringify(port.messages.at(-1)));
  return session;
}

test("debug attach needs an interaction grant and enables the evidence domains", async () => {
  const { api, port } = fakeApi();
  const calls = debuggerFake(api);
  const controller = createController(api);
  controller.connectNative();
  await controller.handleNativeMessage(readyMessage(), port);
  await controller.handleNativeMessage(control("grant", { capabilities: ["read"] }), port);
  await controller.handleNativeMessage(control("debug-attach", { id: "attach-read" }), port);
  assert.equal(port.messages.at(-1).error.code, "interaction-not-granted");
  await controller.stop();
  await grantWithDebugger(controller, port);
  const attached = port.messages.at(-1);
  assert.equal(attached.result.debugger, "attached");
  assert.equal(attached.result.grant.debugger, true);
  assert.equal(port.messages.at(-2).type, "grant");
  assert.equal(port.messages.at(-2).grant.debugger, true);
  assert.deepEqual(calls, [
    ["attach", 7, "1.3"],
    ["send", "Runtime.enable", {}],
    ["send", "Log.enable", {}],
    ["send", "Network.enable", {}],
  ]);
  await controller.handleNativeMessage(control("debug-attach", { id: "attach-again" }), port);
  assert.equal(port.messages.at(-1).error.code, "debugger-active");
  await controller.handleNativeMessage(control("debug-detach", { id: "detach" }), port);
  assert.equal(port.messages.at(-1).result.debugger, "detached");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls.at(-1), ["detach", 7]);
  assert.equal(controller.status().grant.debugger, false);
});

test("debugger evidence keeps failures and request metadata without queries or headers", async () => {
  const { api, port } = fakeApi();
  debuggerFake(api);
  const controller = createController(api);
  controller.connectNative();
  const session = await grantWithDebugger(controller, port);
  const tab = { tabId: 7 };
  const log = (value) => ({ type: "string", value });
  controller.handleDebuggerEvent(tab, "Runtime.consoleAPICalled", { type: "log", args: [log("no")] });
  controller.handleDebuggerEvent(tab, "Runtime.consoleAPICalled", {
    type: "error",
    args: [log("boom"), { type: "number", value: 3 }],
  });
  controller.handleDebuggerEvent(tab, "Runtime.exceptionThrown", {
    exceptionDetails: {
      text: "Uncaught",
      exception: { description: "TypeError: x" },
      url: "http://127.0.0.1/app.js?token=secret#frag",
      lineNumber: 4,
    },
  });
  controller.handleDebuggerEvent(tab, "Log.entryAdded", {
    entry: { level: "warning", source: "network", text: "slow" },
  });
  controller.handleDebuggerEvent({ tabId: 8 }, "Runtime.consoleAPICalled", {
    type: "error",
    args: [log("another tab")],
  });
  controller.handleDebuggerEvent(tab, "Network.requestWillBeSent", {
    requestId: "r1",
    request: {
      method: "POST",
      url: "https://example.test/api/save?session=abc#x",
      headers: { Cookie: "c" },
      postData: "body",
    },
    type: "Fetch",
    wallTime: 1,
  });
  controller.handleDebuggerEvent(tab, "Network.responseReceived", {
    requestId: "r1",
    response: { status: 201, mimeType: "application/json", headers: { "Set-Cookie": "c" } },
  });
  controller.handleDebuggerEvent(tab, "Network.loadingFinished", { requestId: "r1" });
  controller.handleDebuggerEvent(tab, "Network.requestWillBeSent", {
    requestId: "r2",
    request: { method: "GET", url: "http://127.0.0.1:47620/media.webm" },
    type: "Media",
  });
  controller.handleDebuggerEvent(tab, "Network.loadingFailed", {
    requestId: "r2",
    errorText: "net::ERR_ABORTED",
    canceled: true,
  });

  await controller.handleNativeMessage(request("console", { id: "c1", session, payload: {} }), port);
  const evidence = port.messages.at(-1).result;
  assert.equal(evidence.backend, "debugger");
  assert.deepEqual(
    evidence.entries.map((entry) => [entry.kind, entry.text]),
    [
      ["console", "boom 3"],
      ["exception", "TypeError: x"],
    ],
  );
  assert.equal(evidence.entries[1].url, "http://127.0.0.1/app.js");
  await controller.handleNativeMessage(
    request("network", { id: "n1", session, payload: { limit: 5 } }),
    port,
  );
  const network = port.messages.at(-1).result.entries;
  assert.deepEqual(network[0], {
    method: "POST",
    url: "https://example.test/api/save",
    type: "Fetch",
    wall_time: 1,
    status: 201,
    mime_type: "application/json",
    finished: true,
    failure: null,
  });
  assert.deepEqual(network[1].failure, {
    error: "net::ERR_ABORTED",
    canceled: true,
    blocked_reason: null,
  });
  assert.equal(/abc|Cookie|body/.test(JSON.stringify(network)), false);
});

test("accessibility evidence queries by role and name and omits values unless asked", async () => {
  const { api, port } = fakeApi();
  const calls = debuggerFake(api, {
    "DOM.getDocument": { root: { backendNodeId: 1 } },
    "Accessibility.queryAXTree": {
      nodes: [
        {
          nodeId: "10",
          ignored: false,
          role: { value: "button" },
          name: { value: "Apply once" },
          value: { value: "hidden value" },
          properties: [
            { name: "disabled", value: { value: false } },
            { name: "url", value: { value: "x" } },
          ],
          childIds: ["11"],
        },
        { nodeId: "12", ignored: true, role: { value: "generic" } },
      ],
    },
    "Accessibility.getFullAXTree": {
      nodes: [{ nodeId: "20", ignored: false, role: { value: "textbox" }, value: { value: "typed" } }],
    },
  });
  const controller = createController(api);
  controller.connectNative();
  const session = await grantWithDebugger(controller, port);
  await controller.handleNativeMessage(
    request("ax-query", { id: "ax1", session, payload: { role: "button", name: "Apply once" } }),
    port,
  );
  const matched = port.messages.at(-1).result;
  assert.deepEqual(matched.nodes, [
    {
      ax_id: "10",
      role: "button",
      name: "Apply once",
      description: null,
      states: { disabled: false },
      child_count: 1,
    },
  ]);
  assert.equal(matched.values, "omitted");
  assert.deepEqual(calls.find((call) => call[1] === "Accessibility.queryAXTree")[2], {
    backendNodeId: 1,
    role: "button",
    accessibleName: "Apply once",
  });
  await controller.handleNativeMessage(
    request("ax-query", { id: "ax2", session, payload: { include_values: true } }),
    port,
  );
  assert.equal(port.messages.at(-1).result.nodes[0].value, "typed");
});

test("navigation and a detach from the browser both end the debugger", async () => {
  const { api, port } = fakeApi();
  const calls = debuggerFake(api);
  const controller = createController(api);
  controller.connectNative();
  await grantWithDebugger(controller, port, "first");
  await controller.handleTabUpdated(7, { status: "loading" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls.at(-1), ["detach", 7]);
  assert.equal(controller.status().debugger, "detached");

  const session = await grantWithDebugger(controller, port, "second");
  assert.equal(controller.handleDebuggerDetach({ tabId: 7 }, "canceled_by_user"), true);
  assert.equal(controller.status().grant.debugger, false);
  assert.equal(controller.status().debugger_detach_reason, "canceled_by_user");
  await controller.handleNativeMessage(request("console", { id: "c", session, payload: {} }), port);
  assert.equal(port.messages.at(-1).error.code, "debugger-not-attached");
  await controller.handleNativeMessage(control("debug-detach", { id: "d" }), port);
  assert.equal(port.messages.at(-1).error.code, "debugger-not-attached");
});

test("a browser without an extension debugger refuses debugger mode", async () => {
  const { api, port } = fakeApi();
  const controller = createController(api);
  controller.connectNative();
  await controller.handleNativeMessage(
    control("grant", { capabilities: ["read", "interact"] }),
    port,
  );
  const session = port.messages.at(-1).result.grant.session;
  await controller.handleNativeMessage(control("debug-attach", { id: "attach" }), port);
  assert.equal(port.messages.at(-1).error.code, "debugger-unsupported");
  await controller.handleNativeMessage(request("network", { id: "n", session, payload: {} }), port);
  assert.equal(port.messages.at(-1).error.code, "debugger-not-attached");
});

test("a callback-mode collector answers through sendResponse", async () => {
  let listener;
  collectorModule.install(
    { runtime: { onMessage: { addListener: (fn) => (listener = fn) } } },
    new Document([]),
    { respond: "callback" },
  );
  let replied;
  const answered = new Promise((resolve) => (replied = resolve));
  assert.equal(listener({ type: "start", session: "fixture-session" }, {}, replied), true);
  assert.equal((await answered).ok, true);
});

test("a controller clears a badge left by an earlier worker", () => {
  const { api, calls } = fakeApi();
  createController(api);
  assert.deepEqual(calls.badges[0], { text: "" });
});

function collectorPortApi({ acknowledge = true, closeAtOnce = false } = {}) {
  const ports = [];
  let listener;
  const api = {
    runtime: {
      onMessage: { addListener: (fn) => (listener = fn) },
      connect: ({ name }) => {
        const port = {
          name,
          disconnected: false,
          messageListeners: [],
          disconnectListeners: [],
          onMessage: { addListener: (fn) => port.messageListeners.push(fn) },
          onDisconnect: { addListener: (fn) => port.disconnectListeners.push(fn) },
          disconnect() {
            port.disconnected = true;
          },
          close() {
            for (const fn of port.disconnectListeners) fn();
          },
        };
        ports.push(port);
        queueMicrotask(() => {
          if (closeAtOnce) port.close();
          else if (acknowledge) for (const fn of port.messageListeners) fn({ type: "ack" });
        });
        return port;
      },
    },
  };
  const send = (message) => new Promise((resolve) => listener(message, {}, resolve));
  return { api, ports, send };
}

test("an acknowledged collector session ends when its background port closes", async () => {
  const { api, ports, send } = collectorPortApi();
  const collector = collectorModule.install(api, new Document([]), {
    respond: "callback",
    releaseWhenDisconnected: true,
  });
  assert.equal(
    (await send({ type: "start", session: "session-old", capabilities: ["read"] })).ok,
    true,
  );
  assert.deepEqual(
    ports.map((port) => port.name),
    ["cu-collector"],
  );
  const upgrade = await send({
    type: "start",
    session: "session-upgrade",
    capabilities: ["read", "interact"],
  });
  assert.equal(upgrade.ok, false);
  assert.equal(collector.session, "session-old");
  ports[0].close();
  assert.equal(collector.session, null);
  assert.equal(
    (await send({ type: "start", session: "session-new", capabilities: ["read"] })).ok,
    true,
  );
  assert.equal(ports.length, 2);
  assert.equal((await send({ type: "stop", session: "session-new" })).ok, true);
  assert.equal(ports[1].disconnected, true);
});

test("a page restored from the back/forward cache holds no collector session", async () => {
  const { api, ports, send } = collectorPortApi();
  const doc = new Document([]);
  const collector = collectorModule.install(api, doc, {
    respond: "callback",
    releaseWhenDisconnected: true,
  });
  assert.equal(
    (await send({ type: "start", session: "session-left", capabilities: ["read"] })).ok,
    true,
  );
  doc.dispatch("pagehide");
  assert.equal(collector.session, null);
  assert.equal(ports[0].disconnected, true);
  assert.equal(
    (await send({ type: "start", session: "session-restored", capabilities: ["read"] })).ok,
    true,
  );
});

test("a worker that drops or ignores the collector port refuses the session", async () => {
  for (const scenario of [
    { name: "closed at once", closeAtOnce: true },
    { name: "never acknowledged", acknowledge: false },
  ]) {
    const { api, ports, send } = collectorPortApi(scenario);
    const collector = collectorModule.install(api, new Document([]), {
      respond: "callback",
      releaseWhenDisconnected: true,
      ackTimeoutMs: 20,
    });
    const reply = await send({ type: "start", session: "session-1", capabilities: ["read"] });
    assert.equal(reply.ok, false, scenario.name);
    assert.equal(reply.error.code, "worker-mismatch", scenario.name);
    assert.equal(collector.session, null, scenario.name);
    assert.equal(ports[0].disconnected, true, scenario.name);
  }
});

test("the Chromium controller holds collector ports and a closed one ends that tab's grant", async () => {
  const { api, port: nativePort } = fakeApi();
  let onConnect;
  api.runtime.onMessage = { addListener: () => {} };
  api.runtime.onConnect = { addListener: (fn) => (onConnect = fn) };
  api.tabs.onUpdated = { addListener: () => {} };
  api.tabs.onRemoved = { addListener: () => {} };
  const controller = background.install(api, { acceptCollectorPorts: true });
  const port = (name, tabId) => {
    const value = {
      name,
      sender: tabId === undefined ? {} : { tab: { id: tabId } },
      disconnected: false,
      listeners: [],
      messages: [],
      onDisconnect: { addListener: (fn) => value.listeners.push(fn) },
      postMessage(message) {
        value.messages.push(message);
      },
      disconnect() {
        value.disconnected = true;
      },
    };
    return value;
  };
  const other = port("other", 7);
  const tabless = port("cu-collector");
  onConnect(other);
  onConnect(tabless);
  assert.equal(other.disconnected, true);
  assert.equal(tabless.disconnected, true);

  await controller.startCurrent(null, ["read", "interact"]);
  const unrelated = port("cu-collector", 8);
  const granted = port("cu-collector", 7);
  onConnect(unrelated);
  onConnect(granted);
  assert.equal(granted.disconnected, false);
  assert.deepEqual(granted.messages, [{ type: "ack" }]);
  for (const fn of unrelated.listeners) fn();
  await Promise.resolve();
  assert.equal(controller.status().active, true);
  for (const fn of granted.listeners) fn();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(controller.status().active, false);
  assert.equal(nativePort.messages.at(-1).reason, "collector-disconnected");
});

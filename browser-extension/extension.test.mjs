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
    payload: overrides.payload || (operation === "observe" ? { include_values: false, limit: 128 } : {
      search: "", role: null, visible: null, page: 0, page_size: 100, include_values: false,
    }),
  };
}

class FakePort {
  constructor() {
    this.messages = [];
    this.messageListeners = [];
    this.disconnectListeners = [];
    this.onMessage = { addListener: (listener) => this.messageListeners.push(listener) };
    this.onDisconnect = { addListener: (listener) => this.disconnectListeners.push(listener) };
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
    scripting: {
      executeScript: async (value) => calls.execute.push(value),
    },
    tabs: {
      query: async (value) => { calls.query.push(value); return [currentTab]; },
      get: async () => ({ ...currentTab }),
      sendMessage: async (tabId, message) => {
        calls.messages.push({ tabId, message });
        if (message.type === "start") return {
          protocol: PROTOCOL, version: VERSION, type: "started", ok: true,
          session: message.session, document_id: "document-1", generation: 0,
        };
        if (message.type === "read-request") {
          return {
            protocol: PROTOCOL, version: VERSION, type: "response", id: message.request.id,
            session: message.request.session, tab_id: tabId, document_id: "document-1", ok: true,
            result: { kind: "observation", generation: 0, document_id: "document-1" },
          };
        }
        return { protocol: PROTOCOL, version: VERSION, type: "stopped", ok: true };
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
  await controller.startCurrent();
  await port.emit({ ...request("click", { id: "bad-operation" }) });
  assert.equal(port.messages.at(-1).ok, false);
  assert.equal(port.messages.at(-1).error.code, "invalid-request");

  await port.emit({ ...request("observe", { id: "wrong-tab", tab_id: 99 }) });
  assert.equal(port.messages.at(-1).error.code, "tab-not-granted");
  assert.throws(() => validateRequest({ ...request("click") }), /passive read/);
});

class Element {
  constructor(tag, { text = "", attrs = {}, value, rect = { x: 1, y: 2, width: 10, height: 10 }, children = [] } = {}) {
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
    return [...(this._text ? [{nodeType: 3, data: this._text}] : []), ...this.children];
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
    return this.body.querySelectorAll("*").find((element) => element.getAttribute("id") === id) || null;
  }

  dispatch(event) {
    this.listeners.get(event)?.();
    this.defaultView.listeners.get(event)?.();
  }
}

test("collector bounds queries, redacts fields, and labels freshness", () => {
  const email = new Element("input", { attrs: { type: "email", name: "email" }, value: "person@example.test" });
  const password = new Element("input", { attrs: { type: "password", name: "password" }, value: "secret" });
  const launch = new Element("button", { text: "Launch" });
  const doc = new Document([password, email, launch]);
  const collector = createCollector(doc, { idFactory: () => "document-1" });
  const started = collector.handle({ type: "start", session: "session-1" });
  assert.equal(started.document_id, "document-1");

  const observed = collector.handle({ type: "read-request", request: request("observe", {
    payload: { include_values: true, limit: 2 },
  }) });
  assert.equal(observed.ok, true);
  assert.equal(observed.result.elements.truncated, true);
  const fields = observed.result.elements.items.filter((item) => item.tag === "input");
  assert.equal(fields.find((item) => item.value.state === "redacted").value.state, "redacted");
  assert.equal(observed.result.values.state, "available");
  assert.equal(observed.result.limitations.css_projection.state, "unsupported");

  const page = collector.handle({ type: "read-request", request: request("query", {
    payload: { search: "launch", role: "button", visible: true, page: 0, page_size: 1, include_values: false },
  }) });
  assert.equal(page.result.elements.items[0].accessible_name.value, "Launch");
  assert.equal(page.result.pagination.state, "available");
  const emptyPage = collector.handle({ type: "read-request", request: request("query", {
    payload: { search: "launch", role: "button", visible: true, page: 1, page_size: 1, include_values: false },
  }) });
  assert.equal(emptyPage.result.elements.state, "empty");
  const overbound = collector.handle({ type: "read-request", request: request("query", {
    payload: { search: "x".repeat(257), role: null, visible: null, page: 0, page_size: 1, include_values: false },
  }) });
  assert.equal(overbound.ok, false);
  assert.equal(overbound.error.code, "invalid-request");

  const oldGeneration = collector.generation;
  doc.dispatch("input");
  assert.equal(collector.generation, oldGeneration + 1);
  const stale = collector.handle({ type: "read-request", request: request("observe", { generation: oldGeneration }) });
  assert.equal(stale.ok, false);
  assert.equal(stale.error.code, "stale-generation");
  const current = collector.handle({ type: "read-request", request: request("observe", { generation: collector.generation }) });
  assert.equal(current.ok, true);
  collector.dispose();
});


test("document identities are distinct and unknown collector operations are refused", () => {
  const first = createCollector(new Document([]), { idFactory: () => "document-a" });
  const second = createCollector(new Document([]), { idFactory: () => "document-b" });
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
  api.scripting.executeScript = () => new Promise(resolve => { release = resolve; });
  const controller = createController(api);
  const starting = controller.start({ id: 7, windowId: 3, active: true });
  await controller.stop();
  release();
  await starting;
  assert.equal(controller.status().active, false);
});

test("queries can find matches beyond the initial observation page", () => {
  const doc = new Document([
    ...Array.from({length: 150}, () => new Element("button", {text: "Other"})),
    new Element("button", {text: "Needle"}),
  ]);
  const collector = createCollector(doc);
  const result = collector.query({search: "needle", role: "button", visible: true,
    page: 0, page_size: 10, include_values: false});
  assert.equal(result.elements.items.length, 1);
});

test("default text cannot leak textarea values through ancestor text", () => {
  const secret = new Element("textarea", {text: "SENSITIVE_FIXTURE", attrs: {name: "secret"}});
  const collector = createCollector(new Document([new Element("div", {children: [secret]})]));
  const result = collector.observe({include_values: false, limit: 128});
  assert.equal(JSON.stringify(result).includes("SENSITIVE_FIXTURE"), false);
});

test("Firefox message listener returns a Promise, not an ignored plain object", async () => {
  let listener;
  collectorModule.install({runtime: {onMessage: {addListener(fn) { listener = fn; }}}}, new Document([]));
  const reply = listener({type: "start", session: "fixture-session"});
  assert.equal(typeof reply.then, "function");
  assert.equal((await reply).ok, true);
});

test("repeated scope detects programmatic values and preserves whitespace", () => {
  const input = new Element("input", {value: "  old\\nvalue  "});
  const collector = createCollector(new Document([input]), {idFactory: () => "document-1"});
  collector.handle({type: "start", session: "session-1"});
  const message = {type: "read-request", request: request("observe", {payload: {include_values: true, limit: 128}})};
  const before = collector.handle(message);
  assert.equal(before.result.elements.items[0].value.value, "  old\\nvalue  ");
  input.value = "new value";
  message.request.generation = before.result.generation;
  assert.equal(collector.handle(message).error.code, "stale-generation");
});

assert.equal(LIMITS.max_message_bytes, 1024 * 1024);

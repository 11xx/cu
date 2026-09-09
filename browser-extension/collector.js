(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.CUReadCollector = api;
    if (root.browser && root.document && !root.CUReadCollectorInstance) {
      root.CUReadCollectorInstance = api.install(root.browser, root.document);
    }
  }
})(globalThis, function () {
  "use strict";

  const PROTOCOL = "cu-browser-read";
  const VERSION = 1;
  const MAX_TEXT_CHARS = 32768;
  const MAX_ELEMENT_TEXT_CHARS = 512;
  const MAX_VALUE_CHARS = 1024;
  const MAX_ELEMENTS = 128;
  const MAX_SCANNED_NODES = 10000;
  const MAX_QUERY_CHARS = 256;
  const MAX_ROLE_CHARS = 64;
  const MAX_PAGE = 1000;
  const MAX_PAGE_SIZE = 100;
  const READ_OPERATIONS = new Set(["observe", "query"]);
  const OBSERVE_FIELDS = new Set(["include_values", "limit"]);
  const QUERY_FIELDS = new Set(["search", "role", "visible", "page", "page_size", "include_values"]);

  class ValidationError extends Error {
    constructor(message) {
      super(message);
      this.name = "ValidationError";
    }
  }

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function exactKeys(value, required, allowed) {
    if (!isRecord(value)) throw new ValidationError("message must be an object");
    for (const key of required) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) throw new ValidationError(`message is missing ${key}`);
    }
    for (const key of Object.keys(value)) {
      if (!allowed.has(key)) throw new ValidationError(`unsupported message field ${key}`);
    }
  }

  function boundedString(value, name, maximum, { empty = false } = {}) {
    if (typeof value !== "string" || (!empty && value.length === 0) || value.length > maximum) {
      throw new ValidationError(`${name} must be a ${empty ? "" : "non-empty "}string of at most ${maximum} characters`);
    }
    return value;
  }

  function boundedInteger(value, name, maximum, minimum = 0) {
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
      throw new ValidationError(`${name} must be an integer from ${minimum} through ${maximum}`);
    }
    return value;
  }

  function boundedBoolean(value, name) {
    if (typeof value !== "boolean") throw new ValidationError(`${name} must be a boolean`);
    return value;
  }

  function validatePayload(operation, payload) {
    const value = payload === undefined || payload === null ? {} : payload;
    if (!isRecord(value)) throw new ValidationError("payload must be an object");
    const allowed = operation === "observe" ? OBSERVE_FIELDS : QUERY_FIELDS;
    for (const key of Object.keys(value)) {
      if (!allowed.has(key)) throw new ValidationError(`unsupported ${operation} field ${key}`);
    }
    const includeValues = value.include_values === undefined ? false : boundedBoolean(value.include_values, "include_values");
    if (operation === "observe") {
      const limit = value.limit === undefined ? MAX_ELEMENTS : boundedInteger(value.limit, "limit", MAX_ELEMENTS, 1);
      return { include_values: includeValues, limit };
    }
    const search = value.search === undefined ? "" : boundedString(value.search, "search", MAX_QUERY_CHARS, { empty: true });
    const role = value.role === undefined || value.role === null ? null : boundedString(value.role, "role", MAX_ROLE_CHARS);
    const visible = value.visible === undefined || value.visible === null ? null : boundedBoolean(value.visible, "visible");
    const page = value.page === undefined ? 0 : boundedInteger(value.page, "page", MAX_PAGE);
    const pageSize = value.page_size === undefined ? MAX_PAGE_SIZE : boundedInteger(value.page_size, "page_size", MAX_PAGE_SIZE, 1);
    return { search, role, visible, page, page_size: pageSize, include_values: includeValues };
  }

  function validateRequest(message) {
    const allowed = new Set(["protocol", "version", "type", "id", "operation", "session", "tab_id", "document_id", "generation", "payload"]);
    exactKeys(message, allowed, allowed);
    if (message.protocol !== PROTOCOL || message.version !== VERSION || message.type !== "request") {
      throw new ValidationError("unsupported protocol version or message type");
    }
    boundedString(message.id, "id", 128);
    if (!READ_OPERATIONS.has(message.operation)) throw new ValidationError("operation is not a supported passive read");
    boundedString(message.session, "session", 128);
    boundedInteger(message.tab_id, "tab_id", 0x7fffffff, 1);
    boundedString(message.document_id, "document_id", 128);
    if (message.generation !== null) boundedInteger(message.generation, "generation", Number.MAX_SAFE_INTEGER);
    return {
      ...message,
      payload: validatePayload(message.operation, message.payload),
    };
  }

  function secureDocumentId(idFactory) {
    if (idFactory) return boundedString(idFactory(), "document_id", 128);
    if (!globalThis.crypto || typeof globalThis.crypto.randomUUID !== "function") {
      throw new Error("secure randomness is unavailable");
    }
    return `document-${globalThis.crypto.randomUUID()}`;
  }

  function textState(raw, maximum) {
    if (raw === undefined || raw === null) return { state: "unavailable", reason: "text is not exposed" };
    const normalized = String(raw).replace(/\s+/g, " ").trim();
    if (!normalized) return { state: "empty" };
    if (normalized.length > maximum) {
      return { state: "available", value: normalized.slice(0, maximum), truncated: true };
    }
    return { state: "available", value: normalized, truncated: false };
  }

  function documentText(doc) {
    const root = doc.body || doc.documentElement;
    if (!root) return { state: "unavailable", reason: "document has no readable root" };
    return readableText(root, MAX_TEXT_CHARS, MAX_SCANNED_NODES);
  }

  // Never aggregate textContent: it includes hidden text and textarea defaults.
  function readableText(element, maximum = MAX_ELEMENT_TEXT_CHARS, nodeBudget = 512) {
    const parts = [];
    let visited = 0, length = 0;
    function visit(node) {
      if (++visited > nodeBudget || length >= maximum) return;
      if (node.nodeType === 3) {
        const text = node.data.slice(0, maximum - length);
        parts.push(text);
        length += text.length;
        return;
      }
      if (["input", "textarea", "select", "script", "style", "noscript", "iframe"].includes(tagName(node))) return;
      if (node.hidden || attribute(node, "aria-hidden") === "true") return;
      const style = node.ownerDocument?.defaultView?.getComputedStyle(node);
      if (style && (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse")) return;
      for (const child of node.childNodes || []) {
        if (visited >= nodeBudget || length >= maximum) break;
        visit(child);
      }
    }
    visit(element);
    const result = textState(parts.join(" "), maximum);
    result.truncated = Boolean(result.truncated || visited >= nodeBudget || length >= maximum);
    return result;
  }

  function tagName(element) {
    return String(element.localName || element.tagName || "").toLowerCase();
  }

  function attribute(element, name) {
    if (!element || typeof element.getAttribute !== "function") return null;
    const value = element.getAttribute(name);
    return value === null ? null : String(value);
  }

  function implicitRole(element) {
    const explicit = attribute(element, "role");
    if (explicit) return explicit.trim().split(/\s+/, 1)[0].slice(0, MAX_ROLE_CHARS) || null;
    const tag = tagName(element);
    if (tag === "a" && attribute(element, "href") !== null) return "link";
    if (tag === "button") return "button";
    if (/^h[1-6]$/.test(tag)) return "heading";
    if (tag === "img") return "img";
    if (tag === "nav") return "navigation";
    if (tag === "main") return "main";
    if (tag === "form") return "form";
    if (tag === "ul" || tag === "ol") return "list";
    if (tag === "li") return "listitem";
    if (tag === "table") return "table";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return element.multiple ? "listbox" : "combobox";
    if (tag === "option") return "option";
    if (tag === "input") {
      const type = String(attribute(element, "type") || "text").toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (type === "button" || type === "submit" || type === "reset") return "button";
      return "textbox";
    }
    return null;
  }

  function labelText(element, doc) {
    if (element.labels && element.labels.length) {
      const first = element.labels[0];
      return readableText(first);
    }
    if (typeof element.closest === "function") {
      const label = element.closest("label");
      if (label) return readableText(label);
    }
    const labelledBy = attribute(element, "aria-labelledby");
    if (labelledBy && typeof doc.getElementById === "function") {
      const values = labelledBy.split(/\s+/).filter(Boolean).map((id) => {
        const labelled = doc.getElementById(id);
        return labelled ? readableText(labelled).value || "" : "";
      });
      const value = textState(values.join(" "), MAX_ELEMENT_TEXT_CHARS);
      if (value.state !== "empty") return value;
    }
    return null;
  }

  function accessibleName(element, doc) {
    const ariaLabel = attribute(element, "aria-label");
    if (ariaLabel && ariaLabel.trim()) return { state: "available", value: textState(ariaLabel, MAX_ELEMENT_TEXT_CHARS).value, source: "approximate", method: "aria-label" };
    const labelledBy = labelText(element, doc);
    if (labelledBy && labelledBy.state !== "empty") return { ...labelledBy, source: "approximate", method: "label-or-aria-labelledby" };
    const tag = tagName(element);
    if (tag === "img" || tag === "area") {
      const alt = attribute(element, "alt");
      if (alt && alt.trim()) return { state: "available", value: textState(alt, MAX_ELEMENT_TEXT_CHARS).value, source: "approximate", method: "alt" };
    }
    const title = attribute(element, "title");
    if (title && title.trim()) return { state: "available", value: textState(title, MAX_ELEMENT_TEXT_CHARS).value, source: "approximate", method: "title" };
    if (["a", "button", "h1", "h2", "h3", "h4", "h5", "h6", "option", "summary"].includes(tag) || attribute(element, "role")) {
      const text = readableText(element);
      if (text.state !== "empty" && text.state !== "unavailable") return { ...text, source: "approximate", method: "text" };
    }
    const placeholder = attribute(element, "placeholder");
    if (placeholder && placeholder.trim()) return { state: "available", value: textState(placeholder, MAX_ELEMENT_TEXT_CHARS).value, source: "approximate", method: "placeholder" };
    return { state: "empty", source: "approximate", method: null };
  }

  function geometry(element) {
    if (typeof element.getBoundingClientRect !== "function") {
      return { state: "unavailable", reason: "CSS viewport geometry is unavailable" };
    }
    const rect = element.getBoundingClientRect();
    const values = [rect.x, rect.y, rect.width, rect.height];
    if (!values.every((value) => Number.isFinite(value))) {
      return { state: "unavailable", reason: "CSS viewport geometry is not finite" };
    }
    return {
      state: "available",
      space: "css-viewport",
      projection: "unprojectable",
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
    };
  }

  function visibility(element, rect, doc) {
    if (element.hidden || attribute(element, "aria-hidden") === "true") return { state: "hidden", reason: "element is explicitly hidden" };
    const view = doc.defaultView || globalThis;
    if (view && typeof view.getComputedStyle === "function") {
      const style = view.getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse" || style.opacity === "0") {
        return { state: "hidden", reason: "CSS hides the element" };
      }
    }
    if (rect.state === "unavailable") return { state: "unavailable", reason: "visibility cannot be projected without a CSS rect" };
    if (rect.width <= 0 || rect.height <= 0) return { state: "hidden", reason: "element has no CSS viewport area" };
    if (Number.isFinite(view.innerWidth) && Number.isFinite(view.innerHeight) &&
        (rect.x >= view.innerWidth || rect.y >= view.innerHeight || rect.x + rect.width <= 0 || rect.y + rect.height <= 0)) {
      return { state: "offscreen" };
    }
    return { state: "visible", scope: "CSS viewport only; occlusion and compositor visibility unverified" };
  }

  const sensitivePattern = /(?:pass(?:word)?|secret|token|api[\s_-]*key|access[\s_-]*key|auth|otp|one[\s_-]*time|credit[\s_-]*card|card[\s_-]*(?:number|security)|cvv|cvc)/i;
  const sensitiveAutocomplete = new Set([
    "current-password", "new-password", "one-time-code", "cc-number", "cc-exp", "cc-exp-month", "cc-exp-year", "cc-csc",
  ]);

  function isFormControl(element) {
    return ["input", "textarea", "select"].includes(tagName(element));
  }

  function fieldValue(element, includeValues, name) {
    if (!includeValues || !isFormControl(element)) return null;
    const tag = tagName(element);
    const type = String(attribute(element, "type") || "text").toLowerCase();
    const autocomplete = String(element.autocomplete || attribute(element, "autocomplete") || "").toLowerCase().trim();
    const identifiers = [attribute(element, "name"), attribute(element, "id"), attribute(element, "aria-label"), attribute(element, "placeholder"), name?.value].filter(Boolean).join(" ");
    if (type === "password" || sensitivePattern.test(identifiers) || autocomplete.split(/\s+/).some(token => sensitiveAutocomplete.has(token))) {
      return { state: "redacted", reason: "conservative sensitive-field check" };
    }
    if (type === "hidden" || type === "file") return { state: "unsupported", reason: "hidden and file controls are not read" };
    if (typeof element.value !== "string") return { state: "unavailable", reason: "control value is unavailable" };
    if (tag === "select" && element.multiple) return { state: "unsupported", reason: "multiple selections are not exposed" };
    const value = element.value;
    return value.length ? { state: "available", value: value.slice(0, MAX_VALUE_CHARS),
      truncated: value.length > MAX_VALUE_CHARS } : { state: "empty" };
  }

  function elementIterator(root, doc) {
    if (typeof doc.createTreeWalker === "function") {
      const walker = doc.createTreeWalker(root, 1);
      return {
        next() { return walker.nextNode(); },
      };
    }
    const nodes = typeof root.querySelectorAll === "function" ? Array.from(root.querySelectorAll("*")) : [];
    let index = 0;
    return { next() { return nodes[index++] || null; } };
  }

  function describeElement(element, doc, includeValues) {
    const role = implicitRole(element);
    const text = readableText(element);
    const name = accessibleName(element, doc);
    const rect = geometry(element);
    const value = fieldValue(element, includeValues, name);
    return {
      tag: tagName(element),
      role: role ? { state: "available", value: role } : { state: "empty" },
      accessible_name: name,
      text,
      visibility: visibility(element, rect, doc),
      rect,
      ...(value ? { value } : {}),
    };
  }

  function isCandidate(description, element) {
    return description.text.state !== "empty" || description.accessible_name.state !== "empty" || description.role.state !== "empty" || isFormControl(element);
  }

  function collectElements(doc, includeValues, limit, match = () => true, offset = 0) {
    const root = doc.body || doc.documentElement;
    if (!root) return { state: "unavailable", items: [], truncated: false, reason: "document has no readable root" };
    const iterator = elementIterator(root, doc);
    const candidates = [];
    let scanned = 0, matched = 0, exhausted = false;
    while (candidates.length <= limit && scanned < MAX_SCANNED_NODES) {
      const element = iterator.next();
      if (!element) { exhausted = true; break; }
      scanned += 1;
      const description = describeElement(element, doc, includeValues);
      if (isCandidate(description, element) && match(description)) {
        if (matched++ >= offset) candidates.push({ ref: `e${scanned}`, ...description });
      }
    }
    const items = candidates.slice(0, limit);
    return { state: items.length ? "available" : "empty", items, truncated: !exhausted,
      scanned, total: exhausted ? matched : null, has_next: candidates.length > limit,
      scan_budget_exhausted: !exhausted && scanned >= MAX_SCANNED_NODES };
  }

  function limitations() {
    return {
      scope: "top-level document only",
      iframes: { state: "unsupported", reason: "iframe document contents are not traversed" },
      shadow_roots: { state: "unsupported", reason: "shadow-root contents are not traversed" },
      accessibility_tree: { state: "unsupported", reason: "accessible names are an approximation, not a complete accessibility tree" },
      css_projection: { state: "unsupported", reason: "CSS viewport rects have no desktop-frame mapping" },
    };
  }

  function createCollector(doc, { idFactory } = {}) {
    const documentId = secureDocumentId(idFactory);
    let session = null;
    let generation = 0;
    let observer = null;
    const listeners = [];
    const lastReads = new Map();

    function bumpGeneration() {
      generation += 1;
    }

    function installFreshnessObservers() {
      if (typeof doc.defaultView?.addEventListener === "function") {
        for (const event of ["resize", "scroll"]) {
          doc.defaultView.addEventListener(event, bumpGeneration, true);
          listeners.push([doc.defaultView, event]);
        }
      }
      if (typeof doc.addEventListener === "function") {
        for (const event of ["scroll", "input", "change"]) {
          doc.addEventListener(event, bumpGeneration, true);
          listeners.push([doc, event]);
        }
      }
      if (typeof globalThis.MutationObserver === "function" && doc.documentElement) {
        observer = new globalThis.MutationObserver(() => bumpGeneration());
        observer.observe(doc.documentElement, { subtree: true, childList: true, characterData: true, attributes: true });
      }
    }

    function stopObserving() {
      observer?.disconnect();
      observer = null;
      for (const [target, event] of listeners) target.removeEventListener(event, bumpGeneration, true);
      listeners.length = 0;
      lastReads.clear();
    }

    function contextResult(kind, observedGeneration) {
      return {
        protocol: PROTOCOL,
        version: VERSION,
        kind,
        document_id: documentId,
        generation: observedGeneration,
        freshness: { state: "generation-labelled" },
        observed_at: new Date().toISOString(),
        document_visibility: typeof doc.visibilityState === "string" ? doc.visibilityState : "unavailable",
        text: kind === "query" ? { state: "omitted", reason: "query returns matching elements only" } : documentText(doc),
        elements: null,
        values: null,
        limitations: limitations(),
      };
    }

    function observe(payload) {
      const before = generation;
      const result = contextResult("observation", before);
      result.elements = collectElements(doc, payload.include_values, payload.limit);
      result.values = payload.include_values
        ? { state: "available", policy: "nonsecret controls only" }
        : { state: "omitted", reason: "field values require an explicit request" };
      if (generation !== before) {
        result.generation = generation;
        result.freshness = { state: "changed-during-observation" };
      }
      return result;
    }

    function query(payload) {
      const before = generation;
      const result = contextResult("query", before);
      const search = payload.search.toLocaleLowerCase();
      const collection = collectElements(doc, payload.include_values, payload.page_size, (item) => {
        const text = item.text.state === "available" ? item.text.value.toLocaleLowerCase() : "";
        const name = item.accessible_name.state === "available" ? item.accessible_name.value.toLocaleLowerCase() : "";
        const role = item.role.state === "available" ? item.role.value : null;
        return (!search || text.includes(search) || name.includes(search))
          && (!payload.role || role === payload.role)
          && (payload.visible === null || (item.visibility.state === "visible") === payload.visible);
      }, payload.page * payload.page_size);
      result.elements = collection;
      result.pagination = {
        state: collection.truncated ? "truncated" : "available",
        page: payload.page,
        page_size: payload.page_size,
        has_next: collection.has_next,
        scan_budget_exhausted: collection.scan_budget_exhausted,
        total: collection.total,
      };
      result.values = payload.include_values
        ? { state: "available", policy: "nonsecret controls only" }
        : { state: "omitted", reason: "field values require an explicit request" };
      if (generation !== before) {
        result.generation = generation;
        result.freshness = { state: "changed-during-observation" };
      }
      return result;
    }

    function responseFor(request, ok, body) {
      return {
        protocol: PROTOCOL,
        version: VERSION,
        type: "response",
        id: request.id,
        session: request.session,
        tab_id: request.tab_id,
        document_id: documentId,
        ok,
        ...(ok ? { result: body } : { error: body }),
      };
    }

    function refusal(request, code, message) {
      return responseFor(request, false, { code, state: "unsupported", message });
    }

    function handle(message) {
      if (!isRecord(message)) return { ok: false, error: { code: "invalid-message", state: "unsupported", message: "collector message refused" } };
      if (message.type === "start") {
        exactKeys(message, new Set(["type", "session"]), new Set(["type", "session"]));
        session = boundedString(message.session, "session", 128);
        if (!listeners.length) installFreshnessObservers();
        return { protocol: PROTOCOL, version: VERSION, type: "started", ok: true, session, document_id: documentId, generation };
      }
      if (message.type === "stop") {
        exactKeys(message, new Set(["type", "session"]), new Set(["type", "session"]));
        boundedString(message.session, "session", 128);
        if (session !== message.session) return { ok: false, error: { code: "session-mismatch", state: "unsupported", message: "collector session did not match" } };
        session = null;
        stopObserving();
        return { protocol: PROTOCOL, version: VERSION, type: "stopped", ok: true };
      }
      if (message.type !== "read-request") return { ok: false, error: { code: "invalid-message", state: "unsupported", message: "collector message refused" } };
      let request;
      try {
        request = validateRequest(message.request);
      } catch (error) {
        return { ok: false, error: { code: "invalid-request", state: "unsupported", message: "the read request was refused" } };
      }
      if (observer?.takeRecords().length) bumpGeneration();
      if (!session) return refusal(request, "no-session", "the tab has no active read session");
      if (request.session !== session) return refusal(request, "session-mismatch", "the read session did not match");
      if (request.document_id !== documentId) return refusal(request, "stale-document", "the document identity did not match");
      if (request.generation !== null && request.generation !== generation) return refusal(request, "stale-generation", "the observation generation did not match");
      const result = request.operation === "observe" ? observe(request.payload) : query(request.payload);
      // DOM events miss programmatic value assignments. Compare repeated read scopes too.
      const scope = JSON.stringify([request.operation, request.payload]);
      const signature = JSON.stringify([result.text, result.elements, result.document_visibility]);
      if (lastReads.has(scope) && lastReads.get(scope) !== signature) bumpGeneration();
      lastReads.delete(scope);
      lastReads.set(scope, signature);
      if (lastReads.size > 8) lastReads.delete(lastReads.keys().next().value);
      result.generation = generation;
      if (request.generation !== null && request.generation !== generation) {
        return refusal(request, "stale-generation", "the observed content changed");
      }
      return responseFor(request, true, result);
    }

    installFreshnessObservers();

    return {
      handle,
      observe,
      query,
      documentId,
      get generation() { return generation; },
      get session() { return session; },
      dispose: stopObserving,
    };
  }

  function install(api, doc) {
    const collector = createCollector(doc);
    api.runtime.onMessage.addListener(async (message) => collector.handle(message));
    return collector;
  }

  return {
    PROTOCOL,
    VERSION,
    MAX_ELEMENTS,
    READ_OPERATIONS,
    ValidationError,
    validatePayload,
    validateRequest,
    createCollector,
    install,
  };
});

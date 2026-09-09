(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.CUBrowserCollector = api;
    if (root.browser && root.document && !root.CUBrowserCollectorInstance) {
      root.CUBrowserCollectorInstance = api.install(root.browser, root.document);
    }
  }
})(globalThis, function () {
  "use strict";

  const PROTOCOL = "cu-browser-read";
  const VERSION = 2;
  const CAPABILITIES = new Set(["read", "interact"]);
  const PROTOCOL_OPERATIONS = new Set([
    "observe",
    "query",
    "click",
    "focus",
    "fill",
    "scroll",
    "hover",
    "wait",
  ]);
  const READ_OPERATIONS = new Set(["observe", "query"]);
  const ACTION_OPERATIONS = new Set(["click", "focus", "fill", "scroll", "hover"]);
  const MAX_TEXT_CHARS = 32768;
  const MAX_ELEMENT_TEXT_CHARS = 512;
  const MAX_VALUE_CHARS = 1024;
  const MAX_ELEMENTS = 128;
  const MAX_SCANNED_NODES = 10000;
  const MAX_QUERY_CHARS = 256;
  const MAX_SELECTOR_CHARS = 256;
  const MAX_ROLE_CHARS = 64;
  const MAX_REF_CHARS = 64;
  const MAX_PAGE = 1000;
  const MAX_PAGE_SIZE = 100;
  const MAX_SCOPE_MATCHES = 32;
  const MAX_LINKS = 32;
  const MAX_TIME_CANDIDATES = 16;
  const MAX_WAIT_MS = 10000;
  const MAX_WAIT_POLL_MS = 1000;
  const MAX_WAIT_TEXT_CHARS = 512;
  const MAX_ACTION_VALUE_CHARS = 1024;
  const READ_OPERATIONS_FOR_PROTOCOL = new Set(["observe", "query", "wait"]);
  const OBSERVE_FIELDS = new Set(["include_values", "limit", "selector", "include_details"]);
  const QUERY_FIELDS = new Set([
    "search",
    "role",
    "visible",
    "page",
    "page_size",
    "include_values",
    "selector",
    "include_details",
  ]);
  const CAPTURE_FIELDS = new Set();
  const sensitivePattern =
    /(?:pass(?:word)?|secret|token|api[\s_-]*key|access[\s_-]*key|auth|otp|one[\s_-]*time|credit[\s_-]*card|card[\s_-]*(?:number|security)|security[\s_-]*code|verification[\s_-]*code|\bpin\b|cvv|cvc)/i;
  const sensitiveAutocomplete = new Set([
    "current-password",
    "new-password",
    "one-time-code",
    "cc-number",
    "cc-exp",
    "cc-exp-month",
    "cc-exp-year",
    "cc-csc",
  ]);

  class ValidationError extends Error {
    constructor(message) {
      super(message);
      this.name = "ValidationError";
    }
  }
  class WaitTimeout extends Error {
    constructor() {
      super("wait deadline expired");
      this.name = "WaitTimeout";
    }
  }
  class WaitCancelled extends Error {
    constructor() {
      super("wait was cancelled");
      this.name = "WaitCancelled";
    }
  }

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }
  function exactKeys(value, required, allowed) {
    if (!isRecord(value)) throw new ValidationError("message must be an object");
    for (const key of required)
      if (!Object.prototype.hasOwnProperty.call(value, key))
        throw new ValidationError(`message is missing ${key}`);
    for (const key of Object.keys(value))
      if (!allowed.has(key)) throw new ValidationError(`unsupported message field ${key}`);
  }
  function boundedString(value, name, maximum, { empty = false } = {}) {
    if (typeof value !== "string" || (!empty && value.length === 0) || value.length > maximum) {
      throw new ValidationError(
        `${name} must be a ${empty ? "" : "non-empty "}string of at most ${maximum} characters`,
      );
    }
    return value;
  }
  function boundedInteger(value, name, maximum, minimum = 0) {
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum)
      throw new ValidationError(`${name} must be an integer from ${minimum} through ${maximum}`);
    return value;
  }
  function boundedBoolean(value, name) {
    if (typeof value !== "boolean") throw new ValidationError(`${name} must be a boolean`);
    return value;
  }
  function boundedSignedInteger(value, name, maximum) {
    return boundedInteger(value, name, maximum, -maximum);
  }
  function capabilities(value, defaultValue = ["read"]) {
    if (value === undefined) return [...defaultValue];
    if (
      !Array.isArray(value) ||
      value.length === 0 ||
      value.length > CAPABILITIES.size ||
      value.some((item) => typeof item !== "string" || !CAPABILITIES.has(item))
    )
      throw new ValidationError("capabilities must contain only read and interact");
    const result = [...new Set(value)];
    if (result.length !== value.length || !result.includes("read"))
      throw new ValidationError("capabilities must include read and contain no duplicates");
    return result;
  }
  function selectorValue(value) {
    return value === undefined || value === null
      ? null
      : boundedString(value, "selector", MAX_SELECTOR_CHARS);
  }
  function refValue(value) {
    return boundedString(value, "ref", MAX_REF_CHARS);
  }

  function validatePayload(operation, payload) {
    const value = payload === undefined || payload === null ? {} : payload;
    if (!isRecord(value)) throw new ValidationError("payload must be an object");
    let allowed = READ_OPERATIONS.has(operation)
      ? operation === "observe"
        ? OBSERVE_FIELDS
        : QUERY_FIELDS
      : null;
    if (operation === "click" || operation === "focus" || operation === "hover") {
      exactKeys(value, new Set(["ref"]), new Set(["ref"]));
      return { ref: refValue(value.ref) };
    }
    if (operation === "fill") {
      exactKeys(value, new Set(["ref", "value"]), new Set(["ref", "value"]));
      return {
        ref: refValue(value.ref),
        value: boundedString(value.value, "value", MAX_ACTION_VALUE_CHARS, {
          empty: true,
        }),
      };
    }
    if (operation === "scroll") {
      exactKeys(value, new Set(["target", "dx", "dy"]), new Set(["target", "ref", "dx", "dy"]));
      const target = boundedString(value.target, "target", 8);
      if (target !== "document" && target !== "element")
        throw new ValidationError("target must be document or element");
      if (target === "element" && !Object.prototype.hasOwnProperty.call(value, "ref"))
        throw new ValidationError("element scroll requires ref");
      if (target === "document" && Object.prototype.hasOwnProperty.call(value, "ref"))
        throw new ValidationError("document scroll does not accept ref");
      return {
        target,
        ...(target === "element" ? { ref: refValue(value.ref) } : {}),
        dx: boundedSignedInteger(value.dx === undefined ? 0 : value.dx, "dx", 10000),
        dy: boundedSignedInteger(value.dy === undefined ? 0 : value.dy, "dy", 10000),
      };
    }
    if (operation === "wait") {
      const waitFields = new Set([
        "ref",
        "selector",
        "state",
        "text",
        "value",
        "timeout_ms",
        "poll_ms",
      ]);
      exactKeys(value, new Set(["state"]), waitFields);
      const hasRef = Object.prototype.hasOwnProperty.call(value, "ref");
      const selector = selectorValue(value.selector);
      if (hasRef === Boolean(selector))
        throw new ValidationError("wait requires exactly one of ref or selector");
      const state = boundedString(value.state, "state", 16);
      if (
        !["present", "visible", "enabled", "text", "value", "playing", "paused", "ended"].includes(
          state,
        )
      )
        throw new ValidationError("wait state is unsupported");
      if (["text", "value"].includes(state) && value[state] === undefined)
        throw new ValidationError(`${state} wait requires ${state}`);
      if (["playing", "paused", "ended"].includes(state) && !hasRef)
        throw new ValidationError("media waits require ref");
      return {
        ...(hasRef ? { ref: refValue(value.ref) } : { selector }),
        state,
        ...(value.text === undefined
          ? {}
          : {
              text: boundedString(value.text, "text", MAX_WAIT_TEXT_CHARS, {
                empty: true,
              }),
            }),
        ...(value.value === undefined
          ? {}
          : {
              value: boundedString(value.value, "value", MAX_WAIT_TEXT_CHARS, {
                empty: true,
              }),
            }),
        timeout_ms: boundedInteger(
          value.timeout_ms === undefined ? MAX_WAIT_MS : value.timeout_ms,
          "timeout_ms",
          MAX_WAIT_MS,
          1,
        ),
        poll_ms: boundedInteger(
          value.poll_ms === undefined ? 50 : value.poll_ms,
          "poll_ms",
          MAX_WAIT_POLL_MS,
          10,
        ),
      };
    }
    if (!allowed) throw new ValidationError("operation is not supported");
    for (const key of Object.keys(value))
      if (!allowed.has(key)) throw new ValidationError(`unsupported ${operation} field ${key}`);
    const includeValues =
      value.include_values === undefined
        ? false
        : boundedBoolean(value.include_values, "include_values");
    const includeDetails =
      value.include_details === undefined
        ? false
        : boundedBoolean(value.include_details, "include_details");
    const selector = selectorValue(value.selector);
    if (operation === "observe")
      return {
        include_values: includeValues,
        include_details: includeDetails,
        selector,
        limit:
          value.limit === undefined
            ? MAX_ELEMENTS
            : boundedInteger(value.limit, "limit", MAX_ELEMENTS, 1),
      };
    return {
      search:
        value.search === undefined
          ? ""
          : boundedString(value.search, "search", MAX_QUERY_CHARS, {
              empty: true,
            }),
      role:
        value.role === undefined || value.role === null
          ? null
          : boundedString(value.role, "role", MAX_ROLE_CHARS),
      visible:
        value.visible === undefined || value.visible === null
          ? null
          : boundedBoolean(value.visible, "visible"),
      page: value.page === undefined ? 0 : boundedInteger(value.page, "page", MAX_PAGE),
      page_size:
        value.page_size === undefined
          ? MAX_PAGE_SIZE
          : boundedInteger(value.page_size, "page_size", MAX_PAGE_SIZE, 1),
      include_values: includeValues,
      include_details: includeDetails,
      selector,
    };
  }

  function validateRequest(message) {
    const allowed = new Set([
      "protocol",
      "version",
      "type",
      "id",
      "operation",
      "session",
      "tab_id",
      "document_id",
      "generation",
      "capabilities",
      "payload",
    ]);
    exactKeys(
      message,
      new Set([
        "protocol",
        "version",
        "type",
        "id",
        "operation",
        "session",
        "tab_id",
        "document_id",
        "generation",
        "payload",
      ]),
      allowed,
    );
    if (message.protocol !== PROTOCOL || message.version !== VERSION || message.type !== "request")
      throw new ValidationError("unsupported protocol version or message type");
    const operation = boundedString(message.operation, "operation", 32);
    if (!PROTOCOL_OPERATIONS.has(operation))
      throw new ValidationError("operation is not supported");
    const generation = message.generation;
    if (generation !== null) boundedInteger(generation, "generation", Number.MAX_SAFE_INTEGER);
    if (ACTION_OPERATIONS.has(operation) && generation === null)
      throw new ValidationError("actions require an observed generation");
    return {
      protocol: PROTOCOL,
      version: VERSION,
      type: "request",
      id: boundedString(message.id, "id", 128),
      operation,
      session: boundedString(message.session, "session", 128),
      tab_id: boundedInteger(message.tab_id, "tab_id", 0x7fffffff, 1),
      document_id: boundedString(message.document_id, "document_id", 128),
      generation,
      capabilities: capabilities(message.capabilities),
      payload: validatePayload(operation, message.payload),
    };
  }

  function secureDocumentId(idFactory) {
    if (idFactory) return boundedString(idFactory(), "document_id", 128);
    if (!globalThis.crypto || typeof globalThis.crypto.randomUUID !== "function")
      throw new Error("secure randomness is unavailable");
    return `document-${globalThis.crypto.randomUUID()}`;
  }
  function attribute(element, name) {
    if (!element || typeof element.getAttribute !== "function") return null;
    const value = element.getAttribute(name);
    return value === null ? null : String(value);
  }
  function tagName(element) {
    return String(element.localName || element.tagName || "").toLowerCase();
  }
  const overlayNodes = new WeakSet();
  function ownedOverlay(element) {
    return Boolean(element && overlayNodes.has(element));
  }
  function textState(raw, maximum) {
    if (raw === undefined || raw === null)
      return { state: "unavailable", reason: "text is not exposed" };
    const normalized = String(raw).replace(/\s+/g, " ").trim();
    if (!normalized) return { state: "empty" };
    if (normalized.length > maximum)
      return {
        state: "available",
        value: normalized.slice(0, maximum),
        truncated: true,
      };
    return { state: "available", value: normalized, truncated: false };
  }
  function readableText(element, maximum = MAX_ELEMENT_TEXT_CHARS, nodeBudget = 512) {
    const parts = [];
    let visited = 0;
    let length = 0;
    function visit(node) {
      if (++visited > nodeBudget || length >= maximum || ownedOverlay(node)) return;
      if (node.nodeType === 3) {
        const text = node.data.slice(0, maximum - length);
        parts.push(text);
        length += text.length;
        return;
      }
      if (
        ["input", "textarea", "select", "script", "style", "noscript", "iframe"].includes(
          tagName(node),
        )
      )
        return;
      if (node.hidden || attribute(node, "aria-hidden") === "true") return;
      const style = node.ownerDocument?.defaultView?.getComputedStyle?.(node);
      if (
        style &&
        (style.display === "none" ||
          style.visibility === "hidden" ||
          style.visibility === "collapse")
      )
        return;
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
  function documentText(doc) {
    const root = doc.body || doc.documentElement;
    return root
      ? readableText(root, MAX_TEXT_CHARS, MAX_SCANNED_NODES)
      : { state: "unavailable", reason: "document has no readable root" };
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
      if (["button", "submit", "reset"].includes(type)) return "button";
      return "textbox";
    }
    return null;
  }
  function labelText(element, doc) {
    if (element.labels && element.labels.length) return readableText(element.labels[0]);
    if (typeof element.closest === "function") {
      const label = element.closest("label");
      if (label) return readableText(label);
    }
    const labelledBy = attribute(element, "aria-labelledby");
    if (labelledBy && typeof doc.getElementById === "function") {
      const value = textState(
        labelledBy
          .split(/\s+/)
          .filter(Boolean)
          .map((id) => {
            const item = doc.getElementById(id);
            return item ? readableText(item).value || "" : "";
          })
          .join(" "),
        MAX_ELEMENT_TEXT_CHARS,
      );
      if (value.state !== "empty") return value;
    }
    return null;
  }
  function accessibleName(element, doc) {
    const ariaLabel = attribute(element, "aria-label");
    if (ariaLabel && ariaLabel.trim())
      return {
        state: "available",
        value: textState(ariaLabel, MAX_ELEMENT_TEXT_CHARS).value,
        source: "approximate",
        method: "aria-label",
      };
    const labelled = labelText(element, doc);
    if (labelled && labelled.state !== "empty")
      return {
        ...labelled,
        source: "approximate",
        method: "label-or-aria-labelledby",
      };
    const tag = tagName(element);
    if (["img", "area"].includes(tag)) {
      const alt = attribute(element, "alt");
      if (alt && alt.trim())
        return {
          state: "available",
          value: textState(alt, MAX_ELEMENT_TEXT_CHARS).value,
          source: "approximate",
          method: "alt",
        };
    }
    const title = attribute(element, "title");
    if (title && title.trim())
      return {
        state: "available",
        value: textState(title, MAX_ELEMENT_TEXT_CHARS).value,
        source: "approximate",
        method: "title",
      };
    if (
      ["a", "button", "h1", "h2", "h3", "h4", "h5", "h6", "option", "summary"].includes(tag) ||
      attribute(element, "role")
    ) {
      const text = readableText(element);
      if (text.state !== "empty" && text.state !== "unavailable")
        return { ...text, source: "approximate", method: "text" };
    }
    const placeholder = attribute(element, "placeholder");
    if (placeholder && placeholder.trim())
      return {
        state: "available",
        value: textState(placeholder, MAX_ELEMENT_TEXT_CHARS).value,
        source: "approximate",
        method: "placeholder",
      };
    return { state: "empty", source: "approximate", method: null };
  }
  function geometry(element) {
    if (typeof element.getBoundingClientRect !== "function")
      return {
        state: "unavailable",
        reason: "CSS viewport geometry is unavailable",
      };
    const rect = element.getBoundingClientRect();
    const values = [rect.x, rect.y, rect.width, rect.height];
    if (!values.every(Number.isFinite))
      return {
        state: "unavailable",
        reason: "CSS viewport geometry is not finite",
      };
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
    if (ownedOverlay(element) || element.hidden || attribute(element, "aria-hidden") === "true")
      return { state: "hidden", reason: "element is explicitly hidden" };
    const view = doc.defaultView || globalThis;
    const style = view?.getComputedStyle?.(element);
    if (
      style &&
      (style.display === "none" ||
        style.visibility === "hidden" ||
        style.visibility === "collapse" ||
        style.opacity === "0")
    )
      return { state: "hidden", reason: "CSS hides the element" };
    if (rect.state === "unavailable")
      return {
        state: "unavailable",
        reason: "visibility cannot be projected without a CSS rect",
      };
    if (rect.width <= 0 || rect.height <= 0)
      return { state: "hidden", reason: "element has no CSS viewport area" };
    if (
      Number.isFinite(view.innerWidth) &&
      Number.isFinite(view.innerHeight) &&
      (rect.x >= view.innerWidth ||
        rect.y >= view.innerHeight ||
        rect.x + rect.width <= 0 ||
        rect.y + rect.height <= 0)
    )
      return { state: "offscreen" };
    return {
      state: "visible",
      scope: "CSS viewport only; occlusion and compositor visibility unverified",
    };
  }
  function isFormControl(element) {
    return ["input", "textarea", "select"].includes(tagName(element));
  }
  function fieldValue(element, includeValues, name) {
    if (!includeValues || !isFormControl(element)) return null;
    const tag = tagName(element);
    const type = String(attribute(element, "type") || "text").toLowerCase();
    const autocomplete = String(element.autocomplete || attribute(element, "autocomplete") || "")
      .toLowerCase()
      .trim();
    const identifiers = [
      attribute(element, "name"),
      attribute(element, "id"),
      attribute(element, "aria-label"),
      attribute(element, "placeholder"),
      name?.value,
    ]
      .filter(Boolean)
      .join(" ");
    if (
      type === "password" ||
      sensitivePattern.test(identifiers) ||
      autocomplete.split(/\s+/).some((token) => sensitiveAutocomplete.has(token))
    )
      return {
        state: "redacted",
        reason: "conservative sensitive-field check",
      };
    if (type === "hidden" || type === "file")
      return {
        state: "unsupported",
        reason: "hidden and file controls are not read",
      };
    if (typeof element.value !== "string")
      return { state: "unavailable", reason: "control value is unavailable" };
    if (tag === "select" && element.multiple)
      return {
        state: "unsupported",
        reason: "multiple selections are not exposed",
      };
    return element.value.length
      ? {
          state: "available",
          value: element.value.slice(0, MAX_VALUE_CHARS),
          truncated: element.value.length > MAX_VALUE_CHARS,
        }
      : { state: "empty" };
  }
  function boundedLinks(element, doc) {
    const iterator = elementIterator(element, doc);
    const links = [];
    let scanned = 0,
      node;
    while ((node = iterator.next()) && scanned++ < MAX_SCANNED_NODES && links.length <= MAX_LINKS) {
      if (tagName(node) !== "a" || attribute(node, "href") === null) continue;
      const href = attribute(node, "href");
      links.push({
        text: readableText(node, 256).value || "",
        href: href.slice(0, 2048),
        href_truncated: href.length > 2048,
      });
    }
    return {
      state: "available",
      items: links.slice(0, MAX_LINKS),
      truncated: links.length > MAX_LINKS || scanned >= MAX_SCANNED_NODES,
    };
  }
  function timeCandidates(element) {
    const text = readableText(element, MAX_ELEMENT_TEXT_CHARS).value || "";
    const matches =
      text.match(
        /\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b|\b\d+\s*(?:s|sec|secs|second|seconds|min|mins|minute|minutes|h|hr|hour|hours)\b/gi,
      ) || [];
    return {
      state: "available",
      items: [...new Set(matches)].slice(0, MAX_TIME_CANDIDATES).map((value) => ({
        value,
        label: "candidate metadata",
        proven: false,
      })),
      truncated: matches.length > MAX_TIME_CANDIDATES,
    };
  }
  function mediaState(element) {
    if (!["audio", "video"].includes(tagName(element))) return null;
    const duration = Number(element.duration);
    const currentTime = Number(element.currentTime);
    if (!Number.isFinite(duration) || !Number.isFinite(currentTime))
      return {
        state: "unavailable",
        reason: "media timing is not finite",
        paused: Boolean(element.paused),
        ended: Boolean(element.ended),
        ready_state: Number.isSafeInteger(element.readyState) ? element.readyState : null,
      };
    return {
      state: "available",
      duration,
      current_time: currentTime,
      paused: Boolean(element.paused),
      ended: Boolean(element.ended),
      ready_state: Number.isSafeInteger(element.readyState) ? element.readyState : null,
    };
  }
  function elementIterator(root, doc) {
    const walker = doc.createTreeWalker(root, 1);
    let includeRoot = root !== doc.body && root !== doc.documentElement;
    return {
      next() {
        if (includeRoot) {
          includeRoot = false;
          return root;
        }
        return walker.nextNode();
      },
    };
  }
  function describeElement(element, doc, includeValues, includeDetails, ref) {
    const role = implicitRole(element);
    const text = readableText(element);
    const name = accessibleName(element, doc);
    const rect = geometry(element);
    const value = fieldValue(element, includeValues, name);
    return {
      ref,
      tag: tagName(element),
      role: role ? { state: "available", value: role } : { state: "empty" },
      accessible_name: name,
      text,
      visibility: visibility(element, rect, doc),
      rect,
      ...(value ? { value } : {}),
      ...(mediaState(element) ? { media: mediaState(element) } : {}),
      ...(includeDetails
        ? {
            links: boundedLinks(element, doc),
            time_candidates: timeCandidates(element),
          }
        : {}),
    };
  }
  function isCandidate(description, element) {
    return (
      description.text.state !== "empty" ||
      description.accessible_name.state !== "empty" ||
      description.role.state !== "empty" ||
      isFormControl(element) ||
      ["audio", "video"].includes(tagName(element))
    );
  }

  function limitations() {
    return {
      scope: "top-level document only",
      iframes: {
        state: "unsupported",
        reason: "iframe document contents are not traversed",
      },
      shadow_roots: {
        state: "unsupported",
        reason: "shadow-root contents are not traversed",
      },
      accessibility_tree: {
        state: "unsupported",
        reason: "accessible names are an approximation, not a complete accessibility tree",
      },
      css_projection: {
        state: "unsupported",
        reason: "CSS viewport rects have no desktop-frame mapping",
      },
      time_metadata: {
        state: "candidate",
        reason: "text labels are not proven media duration",
      },
    };
  }

  function createCollector(doc, { idFactory } = {}) {
    const documentId = secureDocumentId(idFactory);
    let session = null;
    let generation = 0;
    let observer = null;
    const listeners = [];
    const lastReads = new Map();
    const elementRefs = new WeakMap();
    const refElements = new Map();
    const observedFields = new WeakMap();
    let nextRef = 1;
    let overlay = null;
    let overlayShown = false;
    const waits = new Set();
    function currentField(element) {
      return fieldValue(element, true, accessibleName(element, doc));
    }
    function bumpGeneration() {
      generation += 1;
    }
    function refFor(element) {
      let ref = elementRefs.get(element);
      if (!ref || !refElements.has(ref)) {
        ref = `e${nextRef++}`;
        elementRefs.set(element, ref);
        refElements.set(ref, element);
        if (refElements.size > MAX_SCANNED_NODES)
          refElements.delete(refElements.keys().next().value);
      }
      return ref;
    }
    function isConnected(element) {
      if (!element) return false;
      if (typeof element.isConnected === "boolean") return element.isConnected;
      const root = doc.documentElement || doc.body;
      let current = element;
      let hops = 0;
      while (current && hops++ < MAX_SCANNED_NODES) {
        if (current === root) return true;
        current = current.parentElement;
      }
      return element === root;
    }
    function resolveRef(ref) {
      const element = refElements.get(ref);
      return element && isConnected(element) && !ownedOverlay(element) ? element : null;
    }
    function installOverlay() {
      if (
        !session?.cursor ||
        !session.capabilities.includes("interact") ||
        typeof doc.createElement !== "function" ||
        overlay
      )
        return;
      overlay = doc.createElement("div");
      overlayNodes.add(overlay);
      overlay.setAttribute("data-cu-browser-overlay", "true");
      overlay.setAttribute("aria-hidden", "true");
      overlay.setAttribute("tabindex", "-1");
      overlay.setAttribute("role", "presentation");
      overlay.style.cssText =
        "position:fixed;z-index:2147483647;width:36px;height:36px;pointer-events:none;user-select:none;background:#f6b73c;clip-path:polygon(0 0,100% 38%,52% 52%,38% 100%);filter:drop-shadow(1px 1px 2px #211800);display:none;";
      (doc.body || doc.documentElement)?.appendChild(overlay);
    }
    function removeOverlay() {
      if (overlay?.parentNode) overlay.parentNode.removeChild(overlay);
      overlay = null;
      overlayShown = false;
    }
    function positionOverlay(element) {
      if (!overlay || typeof element.getBoundingClientRect !== "function") return;
      const rect = element.getBoundingClientRect();
      overlay.style.left = `${rect.x + rect.width / 2}px`;
      overlay.style.top = `${rect.y + rect.height / 2}px`;
      overlay.style.display = "block";
      overlayShown = true;
    }
    function setOverlayHidden(hidden) {
      if (overlay) overlay.style.display = hidden || !overlayShown ? "none" : "block";
      return { ok: true };
    }
    function mutationIsOwned(mutation) {
      if (ownedOverlay(mutation.target)) return true;
      const nodes = [...(mutation.addedNodes || []), ...(mutation.removedNodes || [])];
      return mutation.type === "childList" && nodes.length > 0 && nodes.every(ownedOverlay);
    }
    function drainMutations() {
      if (observer?.takeRecords().some((mutation) => !mutationIsOwned(mutation))) bumpGeneration();
    }
    function installFreshnessObservers() {
      if (typeof doc.defaultView?.addEventListener === "function")
        for (const event of ["resize", "scroll"]) {
          doc.defaultView.addEventListener(event, bumpGeneration, true);
          listeners.push([doc.defaultView, event]);
        }
      if (typeof doc.addEventListener === "function") {
        for (const event of ["scroll", "input", "change"]) {
          doc.addEventListener(event, bumpGeneration, true);
          listeners.push([doc, event]);
        }
      }
      const Observer = doc.defaultView?.MutationObserver || globalThis.MutationObserver;
      if (typeof Observer === "function" && doc.documentElement) {
        observer = new Observer((mutations) => {
          if (mutations.some((mutation) => !mutationIsOwned(mutation))) bumpGeneration();
        });
        observer.observe(doc.documentElement, {
          subtree: true,
          childList: true,
          characterData: true,
          attributes: true,
        });
      }
    }
    function stopObserving() {
      observer?.disconnect();
      observer = null;
      for (const [target, event] of listeners)
        target.removeEventListener(event, bumpGeneration, true);
      listeners.length = 0;
      lastReads.clear();
      removeOverlay();
      for (const wait of [...waits]) wait.cancel(new WaitCancelled());
      waits.clear();
      refElements.clear();
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
        document_visibility:
          typeof doc.visibilityState === "string" ? doc.visibilityState : "unavailable",
        text:
          kind === "query"
            ? {
                state: "omitted",
                reason: "query returns matching elements only",
              }
            : documentText(doc),
        elements: null,
        values: null,
        limitations: limitations(),
      };
    }
    function scopeRoots(selector) {
      if (!selector) return [doc.body || doc.documentElement].filter(Boolean);
      try {
        if (typeof doc.querySelectorAll !== "function")
          throw new ValidationError("selector scoping is unavailable");
        const roots = Array.from(doc.querySelectorAll(selector));
        if (roots.length > MAX_SCOPE_MATCHES)
          throw new ValidationError(`selector matches more than ${MAX_SCOPE_MATCHES} containers`);
        return roots;
      } catch (error) {
        if (error instanceof ValidationError) throw error;
        throw new ValidationError("selector is invalid");
      }
    }
    function collectElements(payload, match = () => true, offset = 0) {
      const roots = scopeRoots(payload.selector);
      const candidates = [];
      const limit = payload.limit ?? payload.page_size ?? MAX_ELEMENTS;
      let scanned = 0;
      let matched = 0;
      let exhausted = true;
      for (const root of roots) {
        const iterator = elementIterator(root, doc);
        while (candidates.length <= limit && scanned < MAX_SCANNED_NODES) {
          const element = iterator.next();
          if (!element) break;
          scanned += 1;
          const ref = refFor(element);
          observedFields.set(element, JSON.stringify(currentField(element)));
          const description = describeElement(
            element,
            doc,
            payload.include_values,
            payload.include_details,
            ref,
          );
          if (
            !ownedOverlay(element) &&
            isCandidate(description, element) &&
            match(description, element)
          ) {
            if (matched++ >= offset) candidates.push({ ...description });
          }
        }
        if (candidates.length > limit || scanned >= MAX_SCANNED_NODES) {
          exhausted = false;
          break;
        }
      }
      const items = candidates.slice(0, limit);
      return {
        state: items.length ? "available" : "empty",
        items,
        truncated: !exhausted,
        scanned,
        total: exhausted ? matched : null,
        has_next: candidates.length > limit,
        scan_budget_exhausted: !exhausted && scanned >= MAX_SCANNED_NODES,
      };
    }
    function observe(payload) {
      const before = generation;
      const result = contextResult("observation", before);
      result.elements = collectElements(payload);
      result.values = payload.include_values
        ? { state: "available", policy: "nonsecret controls only" }
        : {
            state: "omitted",
            reason: "field values require an explicit request",
          };
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
      const collection = collectElements(
        payload,
        (item) => {
          const text = item.text.state === "available" ? item.text.value.toLocaleLowerCase() : "";
          const name =
            item.accessible_name.state === "available"
              ? item.accessible_name.value.toLocaleLowerCase()
              : "";
          const role = item.role.state === "available" ? item.role.value : null;
          return (
            (!search || text.includes(search) || name.includes(search)) &&
            (!payload.role || role === payload.role) &&
            (payload.visible === null || (item.visibility.state === "visible") === payload.visible)
          );
        },
        payload.page * payload.page_size,
      );
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
        : {
            state: "omitted",
            reason: "field values require an explicit request",
          };
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
      return responseFor(request, false, {
        code,
        state: "unsupported",
        message,
      });
    }
    function dispatchEvent(element, type) {
      const view = doc.defaultView || globalThis;
      const rect = element.getBoundingClientRect();
      const Constructor = type.startsWith("pointer")
        ? view.PointerEvent
        : type.startsWith("mouse")
          ? view.MouseEvent
          : view.Event;
      if (typeof Constructor !== "function" || typeof element.dispatchEvent !== "function")
        throw new Error("event dispatch is unavailable");
      return element.dispatchEvent(
        new Constructor(type, {
          bubbles: !["mouseenter", "pointerenter"].includes(type),
          cancelable: true,
          clientX: rect.x + rect.width / 2,
          clientY: rect.y + rect.height / 2,
          pointerType: "mouse",
        }),
      );
    }
    function actionable(element) {
      if (!element) throw new ValidationError("element ref is stale or disconnected");
      const description = describeElement(element, doc, false, false, refFor(element));
      if (description.visibility.state !== "visible")
        throw new ValidationError("element is not visible");
      if (element.disabled || attribute(element, "aria-disabled") === "true")
        throw new ValidationError("element is disabled");
      return description;
    }
    function ordinaryInput(element) {
      const tag = tagName(element);
      const type = String(attribute(element, "type") || "text").toLowerCase();
      const identifiers = [
        attribute(element, "name"),
        attribute(element, "id"),
        attribute(element, "aria-label"),
        attribute(element, "placeholder"),
      ]
        .filter(Boolean)
        .join(" ");
      if (!["input", "textarea"].includes(tag))
        throw new ValidationError("fill requires an input or textarea");
      if (
        ["password", "hidden", "file"].includes(type) ||
        sensitivePattern.test(identifiers) ||
        currentField(element)?.state === "redacted"
      )
        throw new ValidationError("sensitive, hidden, and file controls cannot be filled");
      if (tag === "input" && !["text", "search", "email", "url", "tel", "number"].includes(type))
        throw new ValidationError("fill requires an ordinary text field");
      if (currentField(element)?.truncated)
        throw new ValidationError("the existing field value exceeds the freshness bound");
      if (element.readOnly || element.disabled)
        throw new ValidationError("control is read-only or disabled");
      return element;
    }
    function action(request) {
      if (!request.capabilities.includes("interact"))
        return refusal(request, "interaction-not-granted", "the current grant is read-only");
      if (request.generation !== null && request.generation !== generation)
        return refusal(request, "stale-generation", "the observation generation did not match");
      if (request.operation === "scroll" && request.payload.target === "document") {
        const view = doc.defaultView;
        if (!view || typeof view.scrollBy !== "function")
          return refusal(request, "unsupported-action", "document scrolling is unavailable");
        view.scrollBy({ left: request.payload.dx, top: request.payload.dy, behavior: "instant" });
        drainMutations();
        bumpGeneration();
        setOverlayHidden(true);
        overlayShown = false;
        return responseFor(request, true, {
          kind: "action",
          action: "scroll",
          backend: "dom",
          effect: "scroll-call",
          verification: "unverified",
          target: "document",
          generation,
          document_id: documentId,
        });
      }
      const element = resolveRef(request.payload.ref);
      if (!element)
        return refusal(request, "stale-element", "the element ref is stale or disconnected");
      if (
        observedFields.has(element) &&
        observedFields.get(element) !== JSON.stringify(currentField(element))
      ) {
        bumpGeneration();
        return refusal(request, "stale-generation", "the field changed since observation");
      }
      const description = actionable(element);
      if (
        ["input", "textarea"].includes(tagName(element)) &&
        ["redacted", "unsupported"].includes(currentField(element)?.state)
      )
        return refusal(request, "sensitive-target", "sensitive controls cannot be acted on");
      if (request.operation !== "scroll" && typeof doc.elementFromPoint === "function") {
        const rect = element.getBoundingClientRect();
        const width = doc.defaultView.innerWidth;
        const height = doc.defaultView.innerHeight;
        const left = Math.max(0, rect.left),
          right = Math.min(width, rect.right);
        const top = Math.max(0, rect.top),
          bottom = Math.min(height, rect.bottom);
        if (left >= right || top >= bottom)
          return refusal(request, "target-offscreen", "scroll the target into view first");
        const hit = doc.elementFromPoint((left + right) / 2, (top + bottom) / 2);
        if (hit !== element && !element.contains(hit))
          return refusal(request, "target-covered", "the target is covered by another element");
      }
      let verification = "unverified";
      let effect = "event-dispatch";
      if (request.operation === "click") {
        if (typeof element.click !== "function")
          return refusal(request, "unsupported-action", "element click is unavailable");
        element.click();
      } else if (request.operation === "focus") {
        if (typeof element.focus !== "function")
          return refusal(request, "unsupported-action", "element focus is unavailable");
        element.focus();
        verification = doc.activeElement === element ? "verified" : "unverified";
        effect = "focus-call";
      } else if (request.operation === "fill") {
        const input = ordinaryInput(element);
        const prototype =
          tagName(input) === "textarea"
            ? doc.defaultView?.HTMLTextAreaElement?.prototype
            : doc.defaultView?.HTMLInputElement?.prototype;
        const setter = prototype && Object.getOwnPropertyDescriptor(prototype, "value")?.set;
        if (setter) setter.call(input, request.payload.value);
        else input.value = request.payload.value;
        dispatchEvent(input, "input");
        dispatchEvent(input, "change");
        verification = input.value === request.payload.value ? "verified" : "unverified";
        effect = "value-set-and-events";
      } else if (request.operation === "hover") {
        for (const type of [
          "pointerover",
          "pointerenter",
          "mouseover",
          "mouseenter",
          "pointermove",
          "mousemove",
        ])
          dispatchEvent(element, type);
        effect = "synthetic-event-dispatch";
      } else if (request.operation === "scroll") {
        if (typeof element.scrollIntoView !== "function")
          return refusal(request, "unsupported-action", "element scrolling is unavailable");
        if (request.payload.dx || request.payload.dy) {
          if (typeof element.scrollBy !== "function")
            return refusal(request, "unsupported-action", "element scrolling is unavailable");
          element.scrollBy({
            left: request.payload.dx,
            top: request.payload.dy,
            behavior: "instant",
          });
          effect = "element-scroll-call";
        } else {
          element.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
          effect = "scroll-into-view";
        }
      }
      drainMutations();
      bumpGeneration();
      positionOverlay(element);
      return responseFor(request, true, {
        kind: "action",
        action: request.operation,
        ref: request.payload.ref,
        backend: "dom",
        effect,
        verification,
        generation,
        document_id: documentId,
        note:
          request.operation === "hover"
            ? "synthetic event dispatch; not genuine CSS :hover or OS pointer behavior"
            : undefined,
        target: description,
      });
    }
    function condition(element, payload, previousTime) {
      if (!element) return false;
      const description = describeElement(element, doc, true, false, refFor(element));
      if (payload.state === "present") return true;
      if (payload.state === "visible") return description.visibility.state === "visible";
      if (payload.state === "enabled")
        return !element.disabled && attribute(element, "aria-disabled") !== "true";
      if (payload.state === "text")
        return (
          description.text.state === "available" && description.text.value.includes(payload.text)
        );
      if (payload.state === "value")
        return (
          ["available", "empty"].includes(currentField(element)?.state) &&
          element.value === payload.value
        );
      const media = mediaState(element);
      if (!media || media.state !== "available") return false;
      if (payload.state === "playing")
        return (
          !media.paused &&
          !media.ended &&
          media.ready_state >= 2 &&
          media.current_time > previousTime
        );
      if (payload.state === "paused") return media.paused;
      if (payload.state === "ended") return media.ended;
      return false;
    }
    function findWaitElement(payload) {
      if (payload.ref) return resolveRef(payload.ref);
      try {
        const roots = scopeRoots(payload.selector);
        return roots.find((element) => isConnected(element));
      } catch (_) {
        return null;
      }
    }
    function waitFor(request) {
      const payload = request.payload;
      const started = Date.now();
      const deadline = started + payload.timeout_ms;
      return new Promise((resolve) => {
        const wait = {
          id: request.id,
          cancel: (error) => {
            clearTimeout(wait.timer);
            waits.delete(wait);
            resolve(
              refusal(
                request,
                error instanceof WaitCancelled ? "cancelled" : "wait-timeout",
                error.message,
              ),
            );
          },
        };
        const tick = () => {
          if (!session) return wait.cancel(new WaitCancelled());
          drainMutations();
          const element = findWaitElement(payload);
          const previousTime = wait.mediaTime;
          wait.mediaTime = element?.currentTime;
          if (condition(element, payload, previousTime)) {
            waits.delete(wait);
            resolve(
              responseFor(request, true, {
                kind: "wait",
                condition: payload,
                backend: "dom-observation",
                effect: "observation",
                verification: "verified",
                generation,
                document_id: documentId,
                elapsed_ms: Date.now() - started,
              }),
            );
            return;
          }
          if (Date.now() >= deadline) {
            waits.delete(wait);
            resolve(
              refusal(request, "wait-timeout", "the condition was not met before the deadline"),
            );
            return;
          }
          wait.timer = setTimeout(tick, payload.poll_ms);
        };
        waits.add(wait);
        tick();
      });
    }
    function handle(message) {
      if (!isRecord(message))
        return {
          ok: false,
          error: {
            code: "invalid-message",
            state: "unsupported",
            message: "collector message refused",
          },
        };
      if (message.type === "start") {
        exactKeys(
          message,
          new Set(["type", "session"]),
          new Set(["type", "session", "capabilities", "cursor"]),
        );
        const id = boundedString(message.session, "session", 128);
        const granted = capabilities(message.capabilities);
        if (session)
          return {
            ok: false,
            error: { code: "session-active", message: "stop the current grant first" },
          };
        session = { id, capabilities: granted, cursor: message.cursor === true };
        if (!listeners.length) installFreshnessObservers();
        installOverlay();
        return {
          protocol: PROTOCOL,
          version: VERSION,
          type: "started",
          ok: true,
          session: session.id,
          capabilities: [...session.capabilities],
          document_id: documentId,
          generation,
        };
      }
      if (message.type === "stop") {
        exactKeys(message, new Set(["type", "session"]), new Set(["type", "session"]));
        boundedString(message.session, "session", 128);
        if (!session || session.id !== message.session)
          return {
            ok: false,
            error: {
              code: "session-mismatch",
              state: "unsupported",
              message: "collector session did not match",
            },
          };
        session = null;
        stopObserving();
        return {
          protocol: PROTOCOL,
          version: VERSION,
          type: "stopped",
          ok: true,
        };
      }
      if (message.type === "capture-prep" || message.type === "capture-finish") {
        exactKeys(
          message,
          new Set(["type", "session", "document_id", "generation"]),
          new Set(["type", "session", "document_id", "generation"]),
        );
        if (!session || message.session !== session.id)
          return {
            ok: false,
            error: {
              code: "session-mismatch",
              state: "unsupported",
              message: "collector session did not match",
            },
          };
        const preparing = message.type === "capture-prep";
        if (!preparing) setOverlayHidden(false);
        drainMutations();
        if (message.document_id !== documentId || message.generation !== generation)
          return {
            ok: false,
            error: {
              code: message.document_id !== documentId ? "stale-document" : "stale-generation",
            },
          };
        if (!session.capabilities.includes("interact"))
          return { ok: false, error: { code: "interaction-not-granted" } };
        const view = doc.defaultView;
        const result = {
          protocol: PROTOCOL,
          version: VERSION,
          type: message.type,
          ok: true,
          document_id: documentId,
          generation,
          viewport: {
            width: view.innerWidth,
            height: view.innerHeight,
            scroll_x: view.scrollX,
            scroll_y: view.scrollY,
            device_pixel_ratio: view.devicePixelRatio,
            space: "css-viewport",
          },
        };
        if (!preparing) return result;
        setOverlayHidden(true);
        return new Promise((resolve) =>
          view.requestAnimationFrame(() => view.requestAnimationFrame(() => resolve(result))),
        );
      }
      if (message.type === "cancel" && message.session === session?.id) {
        for (const wait of waits) if (wait.id === message.id) wait.cancel(new WaitCancelled());
        return { ok: true };
      }
      if (message.type !== "read-request")
        return {
          ok: false,
          error: {
            code: "invalid-message",
            state: "unsupported",
            message: "collector message refused",
          },
        };
      let request;
      try {
        request = validateRequest(message.request);
      } catch (error) {
        return {
          ok: false,
          error: {
            code: "invalid-request",
            state: "unsupported",
            message: "the browser request was refused",
          },
        };
      }
      drainMutations();
      if (!session) return refusal(request, "no-session", "the tab has no active browser session");
      if (request.session !== session.id)
        return refusal(request, "session-mismatch", "the browser session did not match");
      if (request.document_id !== documentId)
        return refusal(request, "stale-document", "the document identity did not match");
      if (request.capabilities.includes("interact") && !session.capabilities.includes("interact"))
        return refusal(request, "interaction-not-granted", "the current grant is read-only");
      if (request.generation !== null && request.generation !== generation)
        return refusal(request, "stale-generation", "the observation generation did not match");
      if (request.operation === "wait") return waitFor(request);
      try {
        if (!READ_OPERATIONS.has(request.operation)) return action(request);
        const body =
          request.operation === "observe" ? observe(request.payload) : query(request.payload);
        const scope = JSON.stringify([request.operation, request.payload]);
        const signature = JSON.stringify([body.text, body.elements, body.document_visibility]);
        if (lastReads.has(scope) && lastReads.get(scope) !== signature) bumpGeneration();
        lastReads.delete(scope);
        lastReads.set(scope, signature);
        if (lastReads.size > 8) lastReads.delete(lastReads.keys().next().value);
        body.generation = generation;
        if (request.generation !== null && request.generation !== generation)
          return refusal(request, "stale-generation", "the observed content changed");
        return responseFor(request, true, body);
      } catch (error) {
        return refusal(
          request,
          error instanceof ValidationError
            ? "invalid-request"
            : !READ_OPERATIONS.has(request.operation)
              ? "action-uncertain"
              : "collector-error",
          error instanceof ValidationError
            ? error.message
            : "collector execution failed; inspect fresh evidence before any further action",
        );
      }
    }
    installFreshnessObservers();
    return {
      handle,
      observe,
      query,
      documentId,
      get generation() {
        return generation;
      },
      get session() {
        return session?.id || null;
      },
      get overlay() {
        return overlay;
      },
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
    CAPABILITIES,
    MAX_ELEMENTS,
    READ_OPERATIONS,
    ACTION_OPERATIONS,
    ValidationError,
    validatePayload,
    validateRequest,
    createCollector,
    install,
  };
});

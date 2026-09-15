(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.CUBrowserExtension = api;
    if (root.browser) api.install(root.browser);
  }
})(globalThis, function () {
  "use strict";

  const PROTOCOL = "cu-browser-read";
  const VERSION = 9;
  const CAPABILITIES = new Set(["read", "interact"]);
  const READ_OPERATIONS = new Set(["observe", "query", "wait", "ax-query", "console", "network"]);
  // Evidence the worker reads through an attached debugger; collectors never see it.
  const DEBUGGER_READS = new Set(["ax-query", "console", "network"]);
  const CONTROL_OPERATIONS = new Set(["grant", "release", "debug-attach", "debug-detach", "tabs"]);
  const MAX_LISTED_TABS = 128;
  const MAX_DEBUG_ENTRIES = 100;
  const MAX_DEBUG_TEXT_CHARS = 512;
  const AX_STATES = new Set([
    "busy",
    "checked",
    "disabled",
    "expanded",
    "focusable",
    "focused",
    "hidden",
    "invalid",
    "modal",
    "multiselectable",
    "pressed",
    "readonly",
    "required",
    "selected",
  ]);
  // Real input dispatched through an attached debugger rather than page events.
  const TRUSTED_INPUT = new Set([
    "trusted-click",
    "trusted-hover",
    "trusted-type",
    "trusted-key",
    "trusted-scroll",
  ]);
  const TRUSTED_KEYS = Object.freeze({
    Enter: { code: "Enter", keyCode: 13, text: "\r" },
    Tab: { code: "Tab", keyCode: 9 },
    Escape: { code: "Escape", keyCode: 27 },
    Backspace: { code: "Backspace", keyCode: 8 },
    Delete: { code: "Delete", keyCode: 46 },
    ArrowUp: { code: "ArrowUp", keyCode: 38 },
    ArrowDown: { code: "ArrowDown", keyCode: 40 },
    ArrowLeft: { code: "ArrowLeft", keyCode: 37 },
    ArrowRight: { code: "ArrowRight", keyCode: 39 },
    Home: { code: "Home", keyCode: 36 },
    End: { code: "End", keyCode: 35 },
    PageUp: { code: "PageUp", keyCode: 33 },
    PageDown: { code: "PageDown", keyCode: 34 },
    Space: { code: "Space", keyCode: 32, key: " ", text: " " },
  });
  const ACTION_OPERATIONS = new Set([
    "click",
    "focus",
    "fill",
    "scroll",
    "hover",
    "select",
    "capture",
    "reload",
    ...TRUSTED_INPUT,
  ]);
  const OPERATIONS = new Set([...READ_OPERATIONS, ...ACTION_OPERATIONS]);
  const MAX_MESSAGE_BYTES = 1024 * 1024;
  const MAX_QUERY_CHARS = 256;
  const MAX_SELECTOR_CHARS = 256;
  const MAX_ROLE_CHARS = 64;
  const MAX_PAGE = 1000;
  const MAX_PAGE_SIZE = 100;
  const MAX_ELEMENTS = 128;
  const MAX_SESSION_CHARS = 128;
  const MAX_DOCUMENT_ID_CHARS = 128;
  const MAX_REQUEST_ID_CHARS = 128;
  const MAX_GRANTS = 16;
  const MAX_CAPTURE_BYTES = 512 * 1024;
  const MAX_CAPTURE_PIXELS = 16 * 1024 * 1024;
  const CAPTURE_REGION_FIELDS = new Set(["x", "y", "width", "height", "space"]);
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
  const LIMITS = Object.freeze({
    max_message_bytes: MAX_MESSAGE_BYTES,
    max_pending_requests: 64,
    max_query_chars: MAX_QUERY_CHARS,
    max_selector_chars: MAX_SELECTOR_CHARS,
    max_page: MAX_PAGE,
    max_page_size: MAX_PAGE_SIZE,
    max_elements: MAX_ELEMENTS,
    max_capture_bytes: MAX_CAPTURE_BYTES,
    max_capture_pixels: MAX_CAPTURE_PIXELS,
    max_action_value_chars: 1024,
    max_wait_ms: 10000,
    max_wait_poll_ms: 1000,
    default_timeout_seconds: 2,
    max_timeout_seconds: 30,
  });

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
    for (const key of required)
      if (!Object.prototype.hasOwnProperty.call(value, key))
        throw new ValidationError(`message is missing ${key}`);
    for (const key of Object.keys(value))
      if (!allowed.has(key)) throw new ValidationError(`unsupported message field ${key}`);
  }
  function boundedString(value, name, maximum, { empty = false } = {}) {
    if (typeof value !== "string" || (!empty && value.length === 0) || value.length > maximum)
      throw new ValidationError(
        `${name} must be a ${empty ? "" : "non-empty "}string of at most ${maximum} characters`,
      );
    return value;
  }
  function boundedInteger(value, name, maximum, minimum = 0) {
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum)
      throw new ValidationError(`${name} must be an integer from ${minimum} through ${maximum}`);
    return value;
  }
  function boundedSignedInteger(value, name, maximum) {
    return boundedInteger(value, name, maximum, -maximum);
  }
  function boundedBoolean(value, name) {
    if (typeof value !== "boolean") throw new ValidationError(`${name} must be a boolean`);
    return value;
  }
  function boundedNumber(value, name) {
    if (typeof value !== "number" || !Number.isFinite(value))
      throw new ValidationError(`${name} must be a finite number`);
    return value;
  }
  function validateCapabilities(value, defaultValue = ["read"]) {
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
  function validateSelector(value) {
    return value === undefined || value === null
      ? null
      : boundedString(value, "selector", MAX_SELECTOR_CHARS);
  }
  function validateRef(value) {
    return boundedString(value, "ref", 64);
  }
  function validateCaptureRegion(value) {
    exactKeys(value, CAPTURE_REGION_FIELDS, CAPTURE_REGION_FIELDS);
    const region = {
      x: boundedNumber(value.x, "capture region x"),
      y: boundedNumber(value.y, "capture region y"),
      width: boundedNumber(value.width, "capture region width"),
      height: boundedNumber(value.height, "capture region height"),
      space: boundedString(value.space, "capture region space", 32),
    };
    if (region.width <= 0 || region.height <= 0)
      throw new ValidationError("capture region width and height must be positive");
    if (region.space !== "css-viewport")
      throw new ValidationError("capture region space must be css-viewport");
    return region;
  }

  function validatePayload(operation, payload) {
    const value = payload === undefined || payload === null ? {} : payload;
    if (!isRecord(value)) throw new ValidationError("payload must be an object");
    if (operation === "capture") {
      exactKeys(value, new Set(), new Set(["region", "full_page"]));
      if (Object.prototype.hasOwnProperty.call(value, "full_page")) {
        if (Object.prototype.hasOwnProperty.call(value, "region"))
          throw new ValidationError("capture takes a region or the full page, not both");
        if (value.full_page !== true)
          throw new ValidationError("capture full_page must be true when present");
        return { full_page: true };
      }
      return Object.prototype.hasOwnProperty.call(value, "region")
        ? { region: validateCaptureRegion(value.region) }
        : {};
    }
    if (operation === "reload") {
      exactKeys(value, new Set(), new Set());
      return {};
    }
    if (
      operation === "click" ||
      operation === "focus" ||
      operation === "hover" ||
      operation === "trusted-click" ||
      operation === "trusted-hover"
    ) {
      exactKeys(value, new Set(["ref"]), new Set(["ref"]));
      return { ref: validateRef(value.ref) };
    }
    if (operation === "trusted-type") {
      exactKeys(value, new Set(["ref", "text"]), new Set(["ref", "text"]));
      return { ref: validateRef(value.ref), text: boundedString(value.text, "text", 1024) };
    }
    if (operation === "trusted-key") {
      exactKeys(value, new Set(["key"]), new Set(["key"]));
      const key = boundedString(value.key, "key", 16);
      if (!Object.prototype.hasOwnProperty.call(TRUSTED_KEYS, key))
        throw new ValidationError("key is not a supported named key");
      return { key };
    }
    if (operation === "trusted-scroll") {
      exactKeys(value, new Set(["dx", "dy"]), new Set(["ref", "dx", "dy"]));
      return {
        ...(Object.prototype.hasOwnProperty.call(value, "ref") ? { ref: validateRef(value.ref) } : {}),
        dx: boundedSignedInteger(value.dx, "dx", 10000),
        dy: boundedSignedInteger(value.dy, "dy", 10000),
      };
    }
    if (operation === "select") {
      exactKeys(value, new Set(["ref"]), new Set(["ref", "value", "label"]));
      const hasValue = Object.prototype.hasOwnProperty.call(value, "value");
      const hasLabel = Object.prototype.hasOwnProperty.call(value, "label");
      if (hasValue === hasLabel)
        throw new ValidationError("select requires exactly one of value or label");
      const optionField = hasValue ? "value" : "label";
      return {
        ref: validateRef(value.ref),
        [optionField]: boundedString(value[optionField], optionField, 1024, { empty: true }),
      };
    }
    if (operation === "fill") {
      exactKeys(value, new Set(["ref", "value"]), new Set(["ref", "value"]));
      return {
        ref: validateRef(value.ref),
        value: boundedString(value.value, "value", 1024, { empty: true }),
      };
    }
    if (operation === "scroll") {
      exactKeys(value, new Set(["target", "dx", "dy"]), new Set(["target", "ref", "dx", "dy"]));
      const target = boundedString(value.target, "target", 8);
      if (!["document", "element"].includes(target))
        throw new ValidationError("target must be document or element");
      if (target === "element" && !Object.prototype.hasOwnProperty.call(value, "ref"))
        throw new ValidationError("element scroll requires ref");
      if (target === "document" && Object.prototype.hasOwnProperty.call(value, "ref"))
        throw new ValidationError("document scroll does not accept ref");
      return {
        target,
        ...(target === "element" ? { ref: validateRef(value.ref) } : {}),
        dx: boundedSignedInteger(value.dx === undefined ? 0 : value.dx, "dx", 10000),
        dy: boundedSignedInteger(value.dy === undefined ? 0 : value.dy, "dy", 10000),
      };
    }
    if (operation === "console" || operation === "network") {
      exactKeys(value, new Set(), new Set(["limit"]));
      return {
        limit:
          value.limit === undefined
            ? MAX_DEBUG_ENTRIES
            : boundedInteger(value.limit, "limit", MAX_DEBUG_ENTRIES, 1),
      };
    }
    if (operation === "ax-query") {
      exactKeys(value, new Set(), new Set(["role", "name", "include_values", "limit"]));
      return {
        role:
          value.role === undefined || value.role === null
            ? null
            : boundedString(value.role, "role", MAX_ROLE_CHARS),
        name:
          value.name === undefined || value.name === null
            ? null
            : boundedString(value.name, "name", MAX_QUERY_CHARS),
        include_values:
          value.include_values === undefined
            ? false
            : boundedBoolean(value.include_values, "include_values"),
        limit:
          value.limit === undefined
            ? MAX_ELEMENTS
            : boundedInteger(value.limit, "limit", MAX_ELEMENTS, 1),
      };
    }
    if (operation === "wait") {
      const allowed = new Set([
        "ref",
        "selector",
        "state",
        "text",
        "value",
        "timeout_ms",
        "poll_ms",
      ]);
      exactKeys(value, new Set(["state"]), allowed);
      const hasRef = Object.prototype.hasOwnProperty.call(value, "ref");
      const selector = validateSelector(value.selector);
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
        ...(hasRef ? { ref: validateRef(value.ref) } : { selector }),
        state,
        ...(value.text === undefined
          ? {}
          : { text: boundedString(value.text, "text", 512, { empty: true }) }),
        ...(value.value === undefined
          ? {}
          : {
              value: boundedString(value.value, "value", 512, { empty: true }),
            }),
        timeout_ms: boundedInteger(
          value.timeout_ms === undefined ? 10000 : value.timeout_ms,
          "timeout_ms",
          10000,
          1,
        ),
        poll_ms: boundedInteger(
          value.poll_ms === undefined ? 50 : value.poll_ms,
          "poll_ms",
          1000,
          10,
        ),
      };
    }
    const allowed = operation === "observe" ? OBSERVE_FIELDS : QUERY_FIELDS;
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
    const selector = validateSelector(value.selector);
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
    if (!OPERATIONS.has(operation))
      throw new ValidationError("operation is not a supported passive read or browser action");
    const generation = message.generation;
    if (generation !== null) boundedInteger(generation, "generation", Number.MAX_SAFE_INTEGER);
    if (ACTION_OPERATIONS.has(operation) && generation === null)
      throw new ValidationError(
        "browser actions are not passive read operations without an observed generation",
      );
    return {
      protocol: PROTOCOL,
      version: VERSION,
      type: "request",
      id: boundedString(message.id, "id", MAX_REQUEST_ID_CHARS),
      operation,
      session: boundedString(message.session, "session", MAX_SESSION_CHARS),
      tab_id: boundedInteger(message.tab_id, "tab_id", 0x7fffffff, 1),
      document_id: boundedString(message.document_id, "document_id", MAX_DOCUMENT_ID_CHARS),
      generation,
      capabilities: validateCapabilities(message.capabilities),
      payload: validatePayload(operation, message.payload),
    };
  }
  function secureId(prefix) {
    if (!globalThis.crypto || typeof globalThis.crypto.randomUUID !== "function")
      throw new Error("secure randomness is unavailable");
    return `${prefix}-${globalThis.crypto.randomUUID()}`;
  }
  function validRequestId(value) {
    return typeof value === "string" && value.length > 0 && value.length <= MAX_REQUEST_ID_CHARS;
  }
  function validateControl(message) {
    exactKeys(
      message,
      new Set(["protocol", "version", "type", "id", "operation", "tab_id"]),
      new Set([
        "protocol",
        "version",
        "type",
        "id",
        "operation",
        "tab_id",
        "capabilities",
        "cursor",
        "include_titles",
      ]),
    );
    if (message.protocol !== PROTOCOL || message.version !== VERSION || message.type !== "control")
      throw new ValidationError("unsupported session control envelope");
    if (message.include_titles !== undefined) {
      if (message.operation !== "tabs")
        throw new ValidationError(`${message.operation} takes no include_titles`);
      if (typeof message.include_titles !== "boolean")
        throw new ValidationError("include_titles must be a boolean");
    }
    if (message.operation === "tabs" && message.tab_id !== null)
      throw new ValidationError("tabs lists every tab and takes no tab_id");
    if (!validRequestId(message.id)) throw new ValidationError("control id is invalid");
    if (!CONTROL_OPERATIONS.has(message.operation))
      throw new ValidationError("operation is not a supported session control");
    if (message.tab_id !== null && (!Number.isSafeInteger(message.tab_id) || message.tab_id < 1))
      throw new ValidationError("tab_id must be a positive integer or null");
    if (message.cursor !== undefined && typeof message.cursor !== "boolean")
      throw new ValidationError("cursor must be a boolean");
    if (
      message.operation !== "grant" &&
      (message.capabilities !== undefined || message.cursor !== undefined)
    )
      throw new ValidationError(`${message.operation} takes no capabilities or cursor`);
    return {
      id: message.id,
      operation: message.operation,
      tab_id: message.tab_id,
      capabilities: validateCapabilities(message.capabilities),
      cursor: message.cursor,
      include_titles: message.include_titles === true,
    };
  }
  function errorResponse(id, code, message, extra = {}) {
    const response = {
      protocol: PROTOCOL,
      version: VERSION,
      type: "response",
      ok: false,
      error: { code, state: "unsupported", message },
    };
    if (validRequestId(id)) response.id = id;
    return { ...response, ...extra };
  }
  function revokedResponse(id, code, message) {
    const response = errorResponse(id, code, message);
    response.error.state = "grant-revoked";
    return response;
  }
  function validateCollectorInfo(message, session) {
    const allowed = new Set([
      "protocol",
      "version",
      "type",
      "ok",
      "session",
      "capabilities",
      "document_id",
      "generation",
    ]);
    exactKeys(
      message,
      new Set(["protocol", "version", "type", "ok", "session", "document_id", "generation"]),
      allowed,
    );
    if (
      message.protocol !== PROTOCOL ||
      message.version !== VERSION ||
      message.type !== "started" ||
      message.ok !== true
    )
      throw new ValidationError("collector did not start a browser session");
    if (message.session !== session) throw new ValidationError("collector session did not match");
    validateCapabilities(message.capabilities);
    boundedString(message.document_id, "document_id", MAX_DOCUMENT_ID_CHARS);
    boundedInteger(message.generation, "generation", Number.MAX_SAFE_INTEGER);
    return {
      ...message,
      capabilities: validateCapabilities(message.capabilities),
    };
  }
  function validateReady(message) {
    const allowed = new Set(["protocol", "version", "type", "helper_id", "socket", "limits"]);
    exactKeys(message, allowed, allowed);
    if (message.protocol !== PROTOCOL || message.version !== VERSION || message.type !== "ready")
      throw new ValidationError("unsupported native helper ready message");
    boundedString(message.helper_id, "helper_id", 128);
    boundedString(message.socket, "socket", 4096);
    if (
      !isRecord(message.limits) ||
      Object.keys(message.limits).length !== Object.keys(LIMITS).length ||
      !Object.entries(LIMITS).every(([key, value]) => message.limits[key] === value)
    )
      throw new ValidationError("native helper limits do not match");
    return message;
  }
  function validateCollectorResponse(message) {
    const allowed = new Set([
      "protocol",
      "version",
      "type",
      "id",
      "session",
      "tab_id",
      "document_id",
      "ok",
      "result",
      "error",
    ]);
    exactKeys(
      message,
      new Set(["protocol", "version", "type", "id", "session", "tab_id", "document_id", "ok"]),
      allowed,
    );
    if (message.protocol !== PROTOCOL || message.version !== VERSION || message.type !== "response")
      throw new ValidationError("collector response has an unsupported envelope");
    boundedString(message.id, "response id", MAX_REQUEST_ID_CHARS);
    boundedString(message.session, "response session", MAX_SESSION_CHARS);
    boundedInteger(message.tab_id, "response tab_id", 0x7fffffff, 1);
    boundedString(message.document_id, "response document_id", MAX_DOCUMENT_ID_CHARS);
    boundedBoolean(message.ok, "response ok");
    if (message.ok && !isRecord(message.result))
      throw new ValidationError("successful response has no result object");
    if (!message.ok) {
      const error = message.error;
      if (!isRecord(error)) throw new ValidationError("refused response has no error object");
      exactKeys(
        error,
        new Set(["code", "state", "message"]),
        new Set(["code", "state", "message"]),
      );
      boundedString(error.code, "error code", 64);
      boundedString(error.state, "error state", 64);
      boundedString(error.message, "error message", 256);
    }
    return message;
  }
  function grantFor(tab, session, info, grantedCapabilities) {
    return {
      session,
      tab_id: tab.id,
      window_id: tab.windowId,
      document_id: info.document_id,
      generation: info.generation,
      capabilities: validateCapabilities(info.capabilities, grantedCapabilities),
      active: tab.active === true,
      hidden: tab.hidden === true,
      debugger: false,
    };
  }
  function copyGrant(grant) {
    return grant ? { ...grant, capabilities: [...grant.capabilities] } : null;
  }
  function extensionSender(api, sender) {
    return Boolean(sender && !sender.tab && sender.id === api.runtime.id);
  }
  function isAction(operation) {
    return ACTION_OPERATIONS.has(operation);
  }
  // Query strings and fragments often carry tokens, so URLs leave the extension
  // with only the scheme, origin and path.
  function urlWithoutQuery(value) {
    if (typeof value !== "string" || value.length === 0) return null;
    try {
      const url = new URL(value);
      if (!["http:", "https:", "file:"].includes(url.protocol)) return url.protocol;
      const origin = url.origin === "null" ? `${url.protocol}//` : url.origin;
      return `${origin}${url.pathname}`.slice(0, MAX_DEBUG_TEXT_CHARS);
    } catch (_) {
      return "unparseable";
    }
  }
  function clippedText(value) {
    const text = typeof value === "string" ? value : value == null ? "" : String(value);
    return text.length > MAX_DEBUG_TEXT_CHARS ? text.slice(0, MAX_DEBUG_TEXT_CHARS) : text;
  }
  function lineNumber(value) {
    return Number.isSafeInteger(value) ? value : null;
  }
  // Only failures are kept: exceptions, error-level console calls and failed
  // assertions, and error-level browser log entries.
  function consoleEntry(method, params) {
    if (method === "Runtime.exceptionThrown") {
      const details = params?.exceptionDetails || {};
      return {
        kind: "exception",
        level: "error",
        text: clippedText(details.exception?.description || details.text),
        url: urlWithoutQuery(details.url),
        line: lineNumber(details.lineNumber),
      };
    }
    if (method === "Runtime.consoleAPICalled" && ["error", "assert"].includes(params?.type))
      return {
        kind: "console",
        level: params.type,
        text: clippedText(
          (params.args || [])
            .map((arg) => arg?.description ?? (arg?.value === undefined ? arg?.type : arg.value))
            .join(" "),
        ),
        url: null,
        line: null,
      };
    if (method === "Log.entryAdded" && params?.entry?.level === "error")
      return {
        kind: "log",
        level: "error",
        source: clippedText(params.entry.source),
        text: clippedText(params.entry.text),
        url: urlWithoutQuery(params.entry.url),
        line: lineNumber(params.entry.lineNumber),
      };
    return null;
  }

  function pngInfo(dataUrl) {
    if (typeof dataUrl !== "string" || !dataUrl.startsWith("data:image/png;base64,"))
      throw new ValidationError("capture did not return a PNG");
    const encoded = dataUrl.slice("data:image/png;base64,".length);
    if (encoded.length > Math.ceil(MAX_CAPTURE_BYTES / 3) * 4)
      throw Object.assign(new ValidationError("capture exceeds its byte budget"), {
        code: "observation-too-large",
      });
    const bytes = typeof atob === "function" ? atob(encoded) : "";
    if (!bytes || bytes.length > MAX_CAPTURE_BYTES)
      throw new ValidationError("browser capture exceeds the bounded output size");
    if (bytes.length < 24 || bytes.slice(0, 8) !== "\x89PNG\r\n\x1a\n")
      throw new ValidationError("capture is not a PNG");
    const width =
      ((bytes.charCodeAt(16) << 24) |
        (bytes.charCodeAt(17) << 16) |
        (bytes.charCodeAt(18) << 8) |
        bytes.charCodeAt(19)) >>>
      0;
    const height =
      ((bytes.charCodeAt(20) << 24) |
        (bytes.charCodeAt(21) << 16) |
        (bytes.charCodeAt(22) << 8) |
        bytes.charCodeAt(23)) >>>
      0;
    if (!width || !height || width * height > MAX_CAPTURE_PIXELS)
      throw Object.assign(new ValidationError("capture exceeds its pixel budget"), {
        code: "observation-too-large",
      });
    return { encoded, bytes: bytes.length, width, height };
  }

  function viewportGeometry(viewport) {
    if (!isRecord(viewport))
      throw Object.assign(new ValidationError("capture viewport geometry is unsupported"), {
        code: "capture-scale-mismatch",
      });
    let width;
    let height;
    try {
      width = boundedNumber(viewport.width, "capture viewport width");
      height = boundedNumber(viewport.height, "capture viewport height");
    } catch (error) {
      throw Object.assign(new ValidationError("capture viewport geometry is unsupported"), {
        code: "capture-scale-mismatch",
        cause: error,
      });
    }
    if (width <= 0 || height <= 0 || viewport.space !== "css-viewport")
      throw Object.assign(new ValidationError("capture viewport geometry is unsupported"), {
        code: "capture-scale-mismatch",
      });
    if (viewport.device_pixel_ratio !== undefined) {
      let ratio;
      try {
        ratio = boundedNumber(viewport.device_pixel_ratio, "capture device pixel ratio");
      } catch (error) {
        throw Object.assign(new ValidationError("capture device pixel ratio is unsupported"), {
          code: "capture-scale-mismatch",
          cause: error,
        });
      }
      if (ratio <= 0)
        throw Object.assign(new ValidationError("capture device pixel ratio is unsupported"), {
          code: "capture-scale-mismatch",
        });
    }
    return { width, height };
  }

  function validateRegionWithinViewport(region, viewport) {
    if (!region) return;
    const { width, height } = viewportGeometry(viewport);
    const epsilon = 1e-9 * Math.max(1, width, height);
    if (
      region.x < -epsilon ||
      region.y < -epsilon ||
      region.x + region.width > width + epsilon ||
      region.y + region.height > height + epsilon
    )
      throw Object.assign(new ValidationError("capture region is outside the CSS viewport"), {
        code: "unsupported-geometry",
      });
  }

  function imageViewportScale(image, viewport) {
    const { width, height } = viewportGeometry(viewport);
    const x = image.width / width;
    const y = image.height / height;
    const tolerance = 1e-6 * Math.max(1, Math.abs(x), Math.abs(y));
    if (
      !Number.isFinite(x) ||
      !Number.isFinite(y) ||
      x <= 0 ||
      y <= 0 ||
      Math.abs(x - y) > tolerance
    )
      throw Object.assign(
        new ValidationError("capture image and CSS viewport scales do not match"),
        {
          code: "capture-scale-mismatch",
        },
      );
    return { x, y };
  }

  function captureCoverage(region, viewport, image, scale) {
    const { width, height } = viewportGeometry(viewport);
    const requested = region || { x: 0, y: 0, width, height, space: "css-viewport" };
    validateRegionWithinViewport(requested, viewport);
    const left = Math.max(0, Math.floor(requested.x * scale.x + 1e-9));
    const top = Math.max(0, Math.floor(requested.y * scale.y + 1e-9));
    const right = Math.min(
      image.width,
      Math.ceil((requested.x + requested.width) * scale.x - 1e-9),
    );
    const bottom = Math.min(
      image.height,
      Math.ceil((requested.y + requested.height) * scale.y - 1e-9),
    );
    if (left >= right || top >= bottom)
      throw Object.assign(new ValidationError("capture region does not cover an image pixel"), {
        code: "unsupported-geometry",
      });
    const pixels = (right - left) * (bottom - top);
    if (pixels > MAX_CAPTURE_PIXELS)
      throw Object.assign(new ValidationError("cropped browser capture exceeds the pixel budget"), {
        code: "capture-budget",
      });
    return {
      requested,
      source: { x: 0, y: 0, width, height, space: "css-viewport" },
      image_pixels: { x: left, y: top, width: right - left, height: bottom - top },
    };
  }

  function createController(api, options = {}) {
    const collectorFiles = options.collectorFiles || ["collector.js"];
    const action = api.browserAction || api.action;
    const state = {
      grant: null,
      pendingStart: null,
      session: null,
      nativePort: null,
      nativeReady: false,
      nativeSocket: null,
      nativeError: null,
      nativeRetryAt: 0,
      debug: null,
      actionQueue: Promise.resolve(),
      jobs: new Map(),
      captureEpoch: 0,
      transition: null,
    };
    async function updateBadge() {
      const text = state.grant
        ? state.grant.capabilities.includes("interact")
          ? "ACT"
          : "READ"
        : "";
      if (action?.setBadgeText) await action.setBadgeText({ text });
      if (state.grant && action?.setBadgeBackgroundColor)
        await action.setBadgeBackgroundColor({
          color: state.grant.capabilities.includes("interact") ? "#9a6700" : "#276749",
        });
    }
    function postNative(message, port = state.nativePort) {
      if (!port) return false;
      try {
        port.postMessage(message);
        return true;
      } catch (_) {
        if (state.nativePort === port) {
          state.nativeError = "native-unavailable";
          state.nativeReady = false;
        }
        return false;
      }
    }
    function helloMessage() {
      return {
        protocol: PROTOCOL,
        version: VERSION,
        type: "hello",
        grants: state.grant ? [copyGrant(state.grant)] : [],
      };
    }
    function grantMessage() {
      return {
        protocol: PROTOCOL,
        version: VERSION,
        type: "grant",
        grant: copyGrant(state.grant),
      };
    }
    function revokeMessage(grant, reason) {
      return {
        protocol: PROTOCOL,
        version: VERSION,
        type: "revoke",
        session: grant.session,
        tab_id: grant.tab_id,
        document_id: grant.document_id,
        reason,
      };
    }
    // The worker holds its native connection while it runs, so a CU session can
    // reach it without any page being bound. A host that is missing or refused
    // disconnects at once; later browser events retry it, at most this often.
    const NATIVE_RETRY_MS = 5000;
    function connectNative() {
      if (state.nativePort) return;
      if (!api.runtime || typeof api.runtime.connectNative !== "function") {
        state.nativeError = "native-unavailable";
        return;
      }
      if (Date.now() < state.nativeRetryAt) return;
      let port;
      try {
        port = api.runtime.connectNative("org.cu.read");
      } catch (_) {
        state.nativeError = "native-unavailable";
        return;
      }
      state.nativePort = port;
      state.nativeReady = false;
      state.nativeSocket = null;
      port.onMessage.addListener((message) => {
        void handleNativeMessage(message, port);
      });
      port.onDisconnect.addListener(() => {
        if (state.nativePort !== port) return;
        state.nativePort = null;
        state.nativeReady = false;
        state.nativeSocket = null;
        state.nativeError = "native-unavailable";
        state.nativeRetryAt = Date.now() + NATIVE_RETRY_MS;
        void revokeGrant("native-disconnect", true);
      });
      if (!postNative(helloMessage(), port)) {
        try {
          port.disconnect();
        } catch (_) {}
      }
    }
    async function revokeGrant(reason, stopCollector) {
      const grant = state.grant;
      const port = state.nativePort;
      state.pendingStart = null;
      state.transition = null;
      state.grant = null;
      state.session = null;
      state.actionQueue = Promise.resolve();
      for (const job of state.jobs.values()) job.cancelled = true;
      state.jobs.clear();
      dropDebugger();
      if (port && grant) postNative(revokeMessage(grant, reason), port);
      if (grant && stopCollector && api.tabs?.sendMessage) {
        try {
          await api.tabs.sendMessage(grant.tab_id, {
            type: "stop",
            session: grant.session,
          });
        } catch (_) {}
      }
      await updateBadge();
      return { ...status(), reason };
    }
    async function start(tab, requestedCapabilities = ["read"], cursor = false) {
      if (!tab || !Number.isSafeInteger(tab.id) || tab.id < 1)
        return errorResponse(null, "tab-unavailable", "the browser tab could not be identified");
      if (state.grant || state.pendingStart || state.transition)
        return errorResponse(null, "session-active", "stop the current browser session first");
      if (!api.scripting || typeof api.scripting.executeScript !== "function")
        return errorResponse(
          null,
          "collector-unavailable",
          "Firefox scripting support is unavailable",
        );
      let grantedCapabilities;
      try {
        grantedCapabilities = validateCapabilities(requestedCapabilities);
      } catch (_) {
        return errorResponse(null, "invalid-ui-message", "the requested capability was refused");
      }
      const session = secureId("session");
      const attempt = { tabId: tab.id };
      state.pendingStart = attempt;
      let info;
      try {
        await api.scripting.executeScript({
          target: { tabId: tab.id },
          files: collectorFiles,
        });
        info = await api.tabs.sendMessage(tab.id, {
          type: "start",
          session,
          cursor,
          capabilities: grantedCapabilities,
        });
        info = validateCollectorInfo(info, session);
      } catch (_) {
        if (state.pendingStart === attempt) state.pendingStart = null;
        await updateBadge();
        return errorResponse(
          null,
          "collector-unavailable",
          "the current tab cannot host a browser collector",
        );
      }
      if (state.pendingStart !== attempt) {
        await api.tabs.sendMessage(tab.id, { type: "stop", session }).catch(() => {});
        return errorResponse(
          null,
          "activation-cancelled",
          "activation was stopped or the tab navigated",
        );
      }
      state.pendingStart = null;
      state.session = session;
      state.grant = grantFor(tab, session, info, grantedCapabilities);
      await updateBadge();
      if (state.nativeReady) postNative(grantMessage(), state.nativePort);
      else connectNative();
      return {
        ok: true,
        active: true,
        grant: copyGrant(state.grant),
        native: state.nativeReady ? "ready" : state.nativePort ? "starting" : "unavailable",
      };
    }
    api.tabs.onActivated?.addListener(() => {
      state.captureEpoch += 1;
    });
    api.windows?.onFocusChanged?.addListener(() => {
      state.captureEpoch += 1;
    });

    async function startCurrent(tab, requestedCapabilities = ["read"], cursor = false) {
      if (tab) return start(tab, requestedCapabilities, cursor);
      try {
        const tabs = await api.tabs.query({
          active: true,
          currentWindow: true,
        });
        return start(tabs[0], requestedCapabilities, cursor);
      } catch (_) {
        return errorResponse(null, "not-current-tab", "the current tab could not be identified");
      }
    }
    async function currentTab(tabId) {
      if (!api.tabs?.query) throw new ValidationError("active tab could not be identified");
      const tabs = await api.tabs.query({ active: true, currentWindow: true });
      const active = tabs.find(
        (tab) => tab && tab.id === tabId && tab.active === true && tab.hidden !== true,
      );
      if (!active) throw new ValidationError("the granted tab is not the active tab");
      if (typeof api.windows?.getCurrent !== "function")
        throw new ValidationError("browser focus could not be verified");
      const window = await api.windows.getCurrent();
      if (window?.focused !== true || window.id !== active.windowId)
        throw new ValidationError("the browser window is not focused");
      return active;
    }
    async function capture(request, grant, job) {
      const epoch = state.captureEpoch;
      const region = request.payload.region || null;
      const snapshot = async (type) => {
        const reply = await api.tabs.sendMessage(request.tab_id, {
          type,
          session: request.session,
          document_id: request.document_id,
          generation: request.generation,
          ...(region ? { region } : {}),
          ...(request.payload.full_page ? { full_page: true } : {}),
        });
        if (!reply?.ok)
          throw Object.assign(new ValidationError("capture identity could not be verified"), {
            code: reply?.error?.code || "capture-identity-mismatch",
          });
        if (reply.document_id !== request.document_id)
          throw Object.assign(
            new ValidationError("capture document identity could not be verified"),
            {
              code: "stale-document",
            },
          );
        if (reply.generation !== request.generation)
          throw Object.assign(new ValidationError("capture generation could not be verified"), {
            code: "stale-generation",
          });
        return reply;
      };
      const assertCurrent = async () => {
        const tab = await currentTab(request.tab_id);
        if (
          job.cancelled ||
          state.grant !== grant ||
          tab.windowId !== grant.window_id ||
          state.captureEpoch !== epoch
        )
          throw new ValidationError("capture grant is no longer current");
      };
      await assertCurrent();
      if (request.payload.full_page)
        return captureFullPage(request, grant, snapshot, assertCurrent);
      let before, after, image;
      try {
        before = await snapshot("capture-prep");
        await assertCurrent();
        if (typeof api.tabs.captureVisibleTab !== "function")
          throw new ValidationError("Firefox viewport capture is unavailable");
        image = pngInfo(await api.tabs.captureVisibleTab(grant.window_id, { format: "png" }));
        await assertCurrent();
      } finally {
        after = await snapshot("capture-finish");
      }
      await assertCurrent();
      if (JSON.stringify(before.viewport) !== JSON.stringify(after.viewport))
        throw Object.assign(new ValidationError("viewport changed during capture"), {
          code: "stale-generation",
        });
      validateRegionWithinViewport(region, after.viewport);
      const scale = region ? imageViewportScale(image, after.viewport) : null;
      const coverage = captureCoverage(region, after.viewport, image, scale || { x: 1, y: 1 });
      return {
        kind: "capture",
        backend: "tabs.captureVisibleTab",
        effect: "browser-rendered-viewport",
        verification: "identity-bracketed",
        provenance: "browser-rendered, not a CU compositor frame",
        actionable: false,
        input_authority: "none",
        document_id: request.document_id,
        generation: after.generation,
        tab_id: request.tab_id,
        window_id: grant.window_id,
        viewport: after.viewport,
        pixels: { width: image.width, height: image.height, space: "image-pixels" },
        requested_coverage: coverage.requested,
        source_coverage: coverage.source,
        ...(region
          ? {
              capture_scale: {
                x: scale.x,
                y: scale.y,
                space: "css-viewport-to-image-pixels",
              },
              requested_image_pixels: coverage.image_pixels,
            }
          : {}),
        png_base64: image.encoded,
        bytes: image.bytes,
        limitations: {
          active_tab_only: true,
          region: "bounded-css-viewport",
          full_page: "debugger-only",
          stitching: "unsupported",
          compositor_frame: "unsupported",
          atomic_navigation_binding: "unsupported",
          css_to_compositor: "unsupported",
        },
      };
    }
    // The debugger renders beyond the viewport in one call, so the document's
    // size is checked against the pixel budget before anything is rendered.
    async function captureFullPage(request, grant, snapshot, assertCurrent) {
      const debug = state.debug;
      if (!debug || debug.grant !== grant)
        throw Object.assign(new ValidationError("attach the debugger with cu web debug attach"), {
          code: "debugger-not-attached",
        });
      const send = async (method, params) => {
        try {
          return await api.debugger.sendCommand({ tabId: debug.tabId }, method, params);
        } catch (_) {
          throw Object.assign(new ValidationError("the debugger did not render the page"), {
            code: state.debug === debug ? "debugger-command-failed" : "debugger-detached",
          });
        }
      };
      let before, after, image, content;
      try {
        before = await snapshot("capture-prep");
        await assertCurrent();
        const size = (await send("Page.getLayoutMetrics", {}))?.cssContentSize;
        if (
          !isRecord(size) ||
          !Number.isFinite(size.width) ||
          !Number.isFinite(size.height) ||
          size.width <= 0 ||
          size.height <= 0
        )
          throw Object.assign(new ValidationError("the page reported no content size"), {
            code: "unsupported-geometry",
          });
        content = { width: size.width, height: size.height, space: "css-document" };
        const ratio = before.viewport.device_pixel_ratio;
        if (Math.ceil(content.width * ratio) * Math.ceil(content.height * ratio) > MAX_CAPTURE_PIXELS)
          throw Object.assign(new ValidationError("the full page exceeds the pixel budget"), {
            code: "capture-budget",
          });
        const shot = await send("Page.captureScreenshot", {
          format: "png",
          captureBeyondViewport: true,
          clip: { x: 0, y: 0, width: content.width, height: content.height, scale: 1 },
        });
        image = pngInfo(`data:image/png;base64,${shot?.data ?? ""}`);
        await assertCurrent();
      } finally {
        after = await snapshot("capture-finish");
      }
      await assertCurrent();
      if (JSON.stringify(before.viewport) !== JSON.stringify(after.viewport))
        throw Object.assign(new ValidationError("viewport changed during capture"), {
          code: "stale-generation",
        });
      return {
        kind: "capture",
        backend: "debugger",
        effect: "browser-rendered-full-page",
        verification: "identity-bracketed",
        provenance: "browser-rendered, not a CU compositor frame",
        actionable: false,
        input_authority: "none",
        document_id: request.document_id,
        generation: after.generation,
        tab_id: request.tab_id,
        window_id: grant.window_id,
        viewport: after.viewport,
        document_size: content,
        pixels: { width: image.width, height: image.height, space: "image-pixels" },
        png_base64: image.encoded,
        bytes: image.bytes,
        limitations: {
          active_tab_only: true,
          region: "unsupported-with-full-page",
          full_page: "single-debugger-render",
          stitching: "unsupported",
          compositor_frame: "unsupported",
          atomic_navigation_binding: "unsupported",
          css_to_compositor: "unsupported",
        },
      };
    }
    function refuseDetachedJobs(current, port) {
      for (const job of state.jobs.values()) {
        if (job === current || job.cancelled) continue;
        job.cancelled = true;
        postNative(
          revokedResponse(
            job.request.id,
            isAction(job.request.operation) ? "grant-revoked" : "cancelled",
            "a reload revoked the grant before this request completed",
          ),
          port,
        );
      }
    }
    // Reload ends the grant before it reaches the tab: the old document's refs,
    // queued actions, and waits cannot outlive it, and the new document receives
    // nothing until a session grants it again.
    async function reloadGrantedTab(request, grant, port, job) {
      if (typeof api.tabs?.reload !== "function") {
        postNative(
          errorResponse(request.id, "reload-unavailable", "Firefox tab reload is unavailable"),
          port,
        );
        return;
      }
      const revoked = { ...copyGrant(grant), reason: "reload" };
      const transition = { tabId: grant.tab_id };
      state.transition = transition;
      state.grant = null;
      state.session = null;
      refuseDetachedJobs(job, port);
      dropDebugger();
      postNative(revokeMessage(grant, "reload"), port);
      await updateBadge();
      const finish = async (response) => {
        if (state.transition === transition) state.transition = null;
        if (state.nativePort === port) postNative(response, port);
        await updateBadge();
      };
      const abandoned = () => job.cancelled || state.nativePort !== port;
      const cancelled = () =>
        finish(
          revokedResponse(request.id, "cancelled", "the reload was cancelled before dispatch"),
        );
      let collectorStopped = false;
      try {
        const stopped = await api.tabs.sendMessage(grant.tab_id, {
          type: "stop",
          session: grant.session,
        });
        collectorStopped = stopped?.ok === true;
      } catch (_) {}
      if (abandoned()) return cancelled();
      try {
        const tab = await currentTab(grant.tab_id);
        if (tab.windowId !== grant.window_id)
          throw new ValidationError("the granted tab changed browser windows");
      } catch (_) {
        return finish(
          revokedResponse(
            request.id,
            "not-current-tab",
            "the granted tab stopped being current before reload; nothing was reloaded",
          ),
        );
      }
      if (abandoned()) return cancelled();
      try {
        await api.tabs.reload(grant.tab_id);
      } catch (_) {
        return finish(
          revokedResponse(
            request.id,
            "reload-rejected",
            "Firefox reported a reload error; inspect the tab before granting it again",
          ),
        );
      }
      return finish({
        protocol: PROTOCOL,
        version: VERSION,
        type: "response",
        id: request.id,
        session: request.session,
        tab_id: request.tab_id,
        document_id: request.document_id,
        ok: true,
        result: {
          kind: "action",
          action: "reload",
          backend: "tabs",
          document_id: request.document_id,
          generation: request.generation,
          dispatch: "api-resolved",
          verification: "unverified",
          effect: "not-observed",
          revoked_grant: revoked,
          new_document_grant: "none",
          collector_stopped: collectorStopped,
          limitations: {
            page_load: "not-observed",
            new_document_authority: "requires-explicit-user-grant",
            bypass_cache: "unsupported",
          },
        },
      });
    }
    async function executeNativeRequest(request, port, job) {
      const grant = state.grant;
      if (
        !grant ||
        state.nativePort !== port ||
        state.session !== request.session ||
        grant.tab_id !== request.tab_id
      ) {
        postNative(
          errorResponse(request.id, "tab-not-granted", "the tab has no active browser grant"),
          port,
        );
        return;
      }
      if (grant.document_id !== request.document_id) {
        postNative(
          errorResponse(request.id, "stale-document", "the document grant is stale"),
          port,
        );
        return;
      }
      if (DEBUGGER_READS.has(request.operation)) {
        await readDebuggerEvidence(request, grant, port);
        return;
      }
      if (isAction(request.operation)) {
        if (!grant.capabilities.includes("interact")) {
          postNative(
            errorResponse(request.id, "interaction-not-granted", "the current grant is read-only"),
            port,
          );
          return;
        }
        if (request.generation !== grant.generation) {
          postNative(
            errorResponse(
              request.id,
              "stale-generation",
              "the action requires the current observed generation",
            ),
            port,
          );
          return;
        }
        try {
          const tab = await currentTab(request.tab_id);
          if (tab.windowId !== grant.window_id)
            throw new ValidationError("the granted tab changed browser windows");
        } catch (_) {
          postNative(
            errorResponse(
              request.id,
              "not-current-tab",
              "browser actions require the active tab and focused browser window",
            ),
            port,
          );
          return;
        }
      }
      if (job.cancelled || state.grant !== grant || state.nativePort !== port) return;
      if (TRUSTED_INPUT.has(request.operation)) {
        await dispatchTrustedInput(request, grant, port, job);
        return;
      }
      if (request.operation === "reload") {
        await reloadGrantedTab(request, grant, port, job);
        return;
      }
      let collected;
      try {
        if (request.operation === "capture")
          collected = {
            protocol: PROTOCOL,
            version: VERSION,
            type: "response",
            id: request.id,
            session: request.session,
            tab_id: request.tab_id,
            document_id: request.document_id,
            ok: true,
            result: await capture(request, grant, job),
          };
        else {
          collected = await api.tabs.sendMessage(request.tab_id, {
            type: "read-request",
            request: {
              ...request,
              capabilities: copyGrant(grant).capabilities,
            },
          });
          validateCollectorResponse(collected);
        }
      } catch (error) {
        postNative(
          errorResponse(
            request.id,
            error.code ||
              (isAction(request.operation) && request.operation !== "capture"
                ? "action-uncertain"
                : "collector-unavailable"),
            "the granted tab did not return a browser result; do not replay an action",
          ),
          port,
        );
        return;
      }
      if (state.grant !== grant || state.nativePort !== port) return;
      if (
        collected.id !== request.id ||
        collected.session !== request.session ||
        collected.tab_id !== request.tab_id
      ) {
        postNative(
          errorResponse(
            request.id,
            isAction(request.operation) && request.operation !== "capture"
              ? "action-uncertain"
              : "mismatched-response",
            "the browser result did not match the request",
          ),
          port,
        );
        await revokeGrant("mismatched-response", false);
        return;
      }
      if (collected.document_id !== grant.document_id) {
        postNative(
          errorResponse(request.id, "stale-document", "the browser document is no longer granted"),
          port,
        );
        await revokeGrant("document-changed", false);
        return;
      }
      if (!collected.ok) {
        postNative(collected, port);
        return;
      }
      const liveTab = await api.tabs.get(request.tab_id);
      if (state.grant !== grant || state.nativePort !== port) return;
      if (liveTab.windowId !== grant.window_id) {
        await revokeGrant("window-changed", true);
        return;
      }
      grant.active = liveTab.active === true;
      grant.hidden = liveTab.hidden === true;
      const result =
        request.operation === "capture"
          ? collected.result
          : {
              ...collected.result,
              context: {
                source: "background",
                semantics: "browser read; not a visible-desktop screenshot identity",
                tab_id: request.tab_id,
                window_id: grant.window_id,
                tab_active: grant.active,
                tab_hidden: grant.hidden,
                session: request.session,
                document_id: request.document_id,
                css_rects: {
                  space: "css-viewport",
                  projection: "unprojectable",
                },
              },
              ...(request.operation === "select"
                ? {
                    grant_provenance: {
                      state: "explicit",
                      authority: "interaction-grant",
                      capabilities: [...grant.capabilities],
                      session: request.session,
                      tab_id: request.tab_id,
                      window_id: grant.window_id,
                      document_id: request.document_id,
                      generation: collected.result.generation,
                    },
                  }
                : {}),
            };
      const response = { ...collected, result };
      if (new TextEncoder().encode(JSON.stringify(response)).length > MAX_MESSAGE_BYTES) {
        postNative(
          errorResponse(
            request.id,
            "observation-too-large",
            "request a narrower result or smaller capture",
          ),
          port,
        );
        return;
      }
      if (Number.isSafeInteger(result.generation)) {
        state.grant.generation = result.generation;
        postNative(grantMessage(), port);
      }
      postNative(response, port);
    }
    async function handleNativeRequest(message, port) {
      let request;
      try {
        request = validateRequest(message);
      } catch (_) {
        const id = message && message.id;
        if (validRequestId(id))
          postNative(errorResponse(id, "invalid-request", "the browser request was refused"), port);
        return;
      }
      if (state.jobs.has(request.id) || state.jobs.size >= 64) {
        postNative(
          errorResponse(request.id, "request-budget", "too many pending browser requests"),
          port,
        );
        return;
      }
      const job = { request, cancelled: false };
      state.jobs.set(request.id, job);
      const invoke = () => (job.cancelled ? undefined : executeNativeRequest(request, port, job));
      try {
        if (isAction(request.operation)) {
          const pending = state.actionQueue.then(invoke, invoke);
          state.actionQueue = pending.catch(() => {});
          await pending;
        } else await invoke();
      } finally {
        if (state.jobs.get(request.id) === job) state.jobs.delete(request.id);
      }
    }
    // The debugger is attached to one bound tab at a time and never outlives its
    // grant. Its evidence is buffered from attach onward, bounded, and stripped
    // of query strings, headers and bodies.
    function recordDebuggerEvent(source, method, params) {
      const debug = state.debug;
      if (!debug || source?.tabId !== debug.tabId) return;
      const entry = consoleEntry(method, params);
      if (entry) {
        debug.console.push(entry);
        if (debug.console.length > MAX_DEBUG_ENTRIES) {
          debug.console.shift();
          debug.dropped.console += 1;
        }
        return;
      }
      const id = params?.requestId;
      if (typeof id !== "string") return;
      let record = debug.requests.get(id);
      if (method === "Network.requestWillBeSent") {
        if (record) {
          record.url = urlWithoutQuery(params.request?.url);
          return;
        }
        record = {
          method: clippedText(params.request?.method) || null,
          url: urlWithoutQuery(params.request?.url),
          type: typeof params.type === "string" ? params.type : null,
          wall_time: Number.isFinite(params.wallTime) ? params.wallTime : null,
          status: null,
          mime_type: null,
          finished: false,
          failure: null,
        };
        debug.requests.set(id, record);
        if (debug.requests.size > MAX_DEBUG_ENTRIES) {
          debug.requests.delete(debug.requests.keys().next().value);
          debug.dropped.network += 1;
        }
        return;
      }
      if (!record) return;
      if (method === "Network.responseReceived") {
        record.status = lineNumber(params.response?.status);
        record.mime_type = clippedText(params.response?.mimeType) || null;
      } else if (method === "Network.loadingFinished") record.finished = true;
      else if (method === "Network.loadingFailed")
        record.failure = {
          error: clippedText(params.errorText),
          canceled: params.canceled === true,
          blocked_reason: typeof params.blockedReason === "string" ? params.blockedReason : null,
        };
    }
    function dropDebugger() {
      const debug = state.debug;
      if (!debug) return;
      state.debug = null;
      if (typeof api.debugger?.detach === "function")
        Promise.resolve()
          .then(() => api.debugger.detach({ tabId: debug.tabId }))
          .catch(() => {});
    }
    function handleDebuggerDetach(source, reason) {
      const debug = state.debug;
      if (!debug || source?.tabId !== debug.tabId) return false;
      state.debug = null;
      state.debuggerDetachReason = typeof reason === "string" ? reason : "detached";
      if (state.grant === debug.grant) {
        state.grant.debugger = false;
        if (state.nativeReady) postNative(grantMessage(), state.nativePort);
      }
      return true;
    }
    async function attachDebugger(control) {
      const grant = state.grant;
      if (!grant || (control.tab_id !== null && control.tab_id !== grant.tab_id))
        return errorResponse(control.id, "tab-not-granted", "the tab has no active browser grant");
      if (!grant.capabilities.includes("interact"))
        return errorResponse(
          control.id,
          "interaction-not-granted",
          "the debugger needs an interaction grant",
        );
      if (typeof api.debugger?.attach !== "function")
        return errorResponse(
          control.id,
          "debugger-unsupported",
          "this browser offers no extension debugger",
        );
      if (state.debug)
        return errorResponse(control.id, "debugger-active", "the debugger is already attached");
      const debug = {
        tabId: grant.tab_id,
        grant,
        attachedAt: new Date().toISOString(),
        console: [],
        requests: new Map(),
        dropped: { console: 0, network: 0 },
      };
      state.debug = debug;
      try {
        await api.debugger.attach({ tabId: debug.tabId }, "1.3");
        for (const method of ["Runtime.enable", "Log.enable", "Network.enable"])
          await api.debugger.sendCommand({ tabId: debug.tabId }, method, {});
      } catch (error) {
        if (state.debug === debug) dropDebugger();
        return errorResponse(
          control.id,
          "debugger-unavailable",
          clippedText(error?.message || "the debugger could not attach").slice(0, 256),
        );
      }
      if (state.debug !== debug || state.grant !== grant) {
        if (state.debug === debug) dropDebugger();
        return revokedResponse(
          control.id,
          "grant-revoked",
          "the grant ended while the debugger attached",
        );
      }
      grant.debugger = true;
      state.debuggerDetachReason = null;
      if (state.nativeReady) postNative(grantMessage(), state.nativePort);
      return null;
    }
    async function accessibilityNodes(debug, payload) {
      const send = (method, params) =>
        api.debugger.sendCommand({ tabId: debug.tabId }, method, params);
      let nodes;
      if (payload.role !== null || payload.name !== null) {
        const { root } = await send("DOM.getDocument", { depth: 0 });
        nodes = (
          await send("Accessibility.queryAXTree", {
            backendNodeId: root.backendNodeId,
            ...(payload.role !== null ? { role: payload.role } : {}),
            ...(payload.name !== null ? { accessibleName: payload.name } : {}),
          })
        ).nodes;
      } else nodes = (await send("Accessibility.getFullAXTree", {})).nodes;
      const included = (Array.isArray(nodes) ? nodes : []).filter(
        (node) => isRecord(node) && node.ignored !== true,
      );
      return {
        nodes: included.slice(0, payload.limit).map((node) => ({
          ax_id: clippedText(node.nodeId),
          role: clippedText(node.role?.value) || null,
          name: clippedText(node.name?.value) || null,
          description: clippedText(node.description?.value) || null,
          ...(payload.include_values
            ? { value: node.value === undefined ? null : clippedText(node.value.value) }
            : {}),
          states: Object.fromEntries(
            (Array.isArray(node.properties) ? node.properties : [])
              .filter((property) => AX_STATES.has(property?.name))
              .map((property) => [property.name, property.value?.value ?? null]),
          ),
          child_count: Array.isArray(node.childIds) ? node.childIds.length : 0,
        })),
        total: included.length,
        truncated: included.length > payload.limit,
        values: payload.include_values ? "included" : "omitted",
      };
    }
    async function readDebuggerEvidence(request, grant, port) {
      const debug = state.debug;
      if (!debug || debug.grant !== grant) {
        postNative(
          errorResponse(
            request.id,
            "debugger-not-attached",
            "attach the debugger with cu web debug attach",
          ),
          port,
        );
        return;
      }
      let evidence;
      try {
        if (request.operation === "console")
          evidence = {
            entries: debug.console.slice(-request.payload.limit),
            dropped: debug.dropped.console,
          };
        else if (request.operation === "network")
          evidence = {
            entries: [...debug.requests.values()]
              .slice(-request.payload.limit)
              .map((entry) => ({ ...entry })),
            dropped: debug.dropped.network,
          };
        else evidence = await accessibilityNodes(debug, request.payload);
      } catch (_) {
        postNative(
          errorResponse(
            request.id,
            state.debug === debug ? "debugger-command-failed" : "debugger-detached",
            "the debugger did not return evidence",
          ),
          port,
        );
        return;
      }
      if (state.grant !== grant || state.nativePort !== port) return;
      const response = {
        protocol: PROTOCOL,
        version: VERSION,
        type: "response",
        id: request.id,
        session: request.session,
        tab_id: request.tab_id,
        document_id: request.document_id,
        ok: true,
        result: {
          kind: "debugger-evidence",
          evidence: request.operation,
          backend: "debugger",
          attached_at: debug.attachedAt,
          document_id: grant.document_id,
          generation: grant.generation,
          ...evidence,
        },
      };
      if (new TextEncoder().encode(JSON.stringify(response)).length > MAX_MESSAGE_BYTES) {
        postNative(
          errorResponse(request.id, "observation-too-large", "request a smaller limit"),
          port,
        );
        return;
      }
      postNative(response, port);
    }
    // Real input goes through the debugger to the point the collector checked,
    // so DOM actions' freshness, visibility, sensitivity and covering refusals
    // apply before anything is dispatched.
    async function dispatchTrustedInput(request, grant, port, job) {
      const debug = state.debug;
      if (!debug || debug.grant !== grant) {
        postNative(
          errorResponse(
            request.id,
            "debugger-not-attached",
            "attach the debugger with cu web debug attach",
          ),
          port,
        );
        return;
      }
      let point = null;
      if (request.operation !== "trusted-key") {
        let located;
        try {
          located = await api.tabs.sendMessage(request.tab_id, {
            type: "target-point",
            request: { ...request, capabilities: copyGrant(grant).capabilities },
          });
          validateCollectorResponse(located);
        } catch (_) {
          postNative(
            errorResponse(
              request.id,
              "collector-unavailable",
              "the bound tab did not locate the target; nothing was dispatched",
            ),
            port,
          );
          return;
        }
        if (!located.ok) {
          postNative(located, port);
          return;
        }
        point = located.result;
      }
      if (job.cancelled || state.debug !== debug || state.grant !== grant) return;
      const input = (method, params) =>
        api.debugger.sendCommand({ tabId: debug.tabId }, method, params);
      const payload = request.payload;
      let dispatched = false;
      let effect;
      try {
        if (request.operation === "trusted-key") {
          const key = TRUSTED_KEYS[payload.key];
          const common = {
            key: key.key ?? payload.key,
            code: key.code,
            windowsVirtualKeyCode: key.keyCode,
            nativeVirtualKeyCode: key.keyCode,
          };
          dispatched = true;
          await input("Input.dispatchKeyEvent", {
            type: key.text ? "keyDown" : "rawKeyDown",
            ...common,
            ...(key.text ? { text: key.text, unmodifiedText: key.text } : {}),
          });
          await input("Input.dispatchKeyEvent", { type: "keyUp", ...common });
          effect = "key-dispatched";
        } else if (request.operation === "trusted-scroll") {
          dispatched = true;
          await input("Input.dispatchMouseEvent", {
            type: "mouseWheel",
            x: point.x,
            y: point.y,
            deltaX: payload.dx,
            deltaY: payload.dy,
          });
          effect = "wheel-dispatched";
        } else {
          const at = { x: point.x, y: point.y };
          dispatched = true;
          await input("Input.dispatchMouseEvent", { type: "mouseMoved", ...at, button: "none" });
          if (request.operation !== "trusted-hover") {
            const press = { ...at, button: "left", clickCount: 1 };
            await input("Input.dispatchMouseEvent", { type: "mousePressed", ...press, buttons: 1 });
            await input("Input.dispatchMouseEvent", { type: "mouseReleased", ...press, buttons: 0 });
          }
          if (request.operation === "trusted-type")
            await input("Input.insertText", { text: payload.text });
          effect = {
            "trusted-hover": "pointer-moved",
            "trusted-click": "click-dispatched",
            "trusted-type": "text-inserted",
          }[request.operation];
        }
      } catch (_) {
        postNative(
          dispatched
            ? errorResponse(
                request.id,
                "action-uncertain",
                "debugger input may have been partially applied; inspect fresh evidence",
              )
            : errorResponse(request.id, "debugger-detached", "the debugger detached first"),
          port,
        );
        return;
      }
      if (state.grant !== grant || state.nativePort !== port) return;
      postNative(
        {
          protocol: PROTOCOL,
          version: VERSION,
          type: "response",
          id: request.id,
          session: request.session,
          tab_id: request.tab_id,
          document_id: request.document_id,
          ok: true,
          result: {
            kind: "action",
            action: request.operation,
            backend: "debugger",
            effect,
            verification: "unverified",
            ...(payload.ref ? { ref: payload.ref } : {}),
            ...(point ? { point: { x: point.x, y: point.y, space: "css-viewport" } } : {}),
            generation: point ? point.generation : grant.generation,
            document_id: request.document_id,
          },
        },
        port,
      );
    }
    async function storedCursor() {
      try {
        const stored = await api.storage?.local?.get({ cursor: true });
        return stored?.cursor !== false;
      } catch (_) {
        return true;
      }
    }
    // A CU session binds and releases tabs over the helper socket, which only
    // the local user can reach; installing the extension is the standing opt-in.
    async function handleNativeControl(message, port) {
      let control;
      try {
        control = validateControl(message);
      } catch (_) {
        if (validRequestId(message?.id))
          postNative(
            errorResponse(message.id, "invalid-request", "the session control was refused"),
            port,
          );
        return;
      }
      const reply = (grant, result) =>
        postNative(
          {
            protocol: PROTOCOL,
            version: VERSION,
            type: "response",
            id: control.id,
            session: grant.session,
            tab_id: grant.tab_id,
            document_id: grant.document_id,
            ok: true,
            result,
          },
          port,
        );
      if (control.operation === "debug-attach") {
        const refused = await attachDebugger(control);
        if (refused) postNative(refused, port);
        else
          reply(state.grant, {
            grant: copyGrant(state.grant),
            debugger: "attached",
            attached_at: state.debug.attachedAt,
          });
        return;
      }
      if (control.operation === "debug-detach") {
        const grant = state.grant;
        if (!state.debug || !grant || (control.tab_id !== null && control.tab_id !== grant.tab_id)) {
          postNative(
            errorResponse(control.id, "debugger-not-attached", "no debugger is attached to that tab"),
            port,
          );
          return;
        }
        dropDebugger();
        grant.debugger = false;
        if (state.nativeReady) postNative(grantMessage(), port);
        reply(grant, { grant: copyGrant(grant), debugger: "detached" });
        return;
      }
      if (control.operation === "tabs") {
        let tabs;
        try {
          tabs = await api.tabs.query({});
        } catch (_) {
          postNative(
            errorResponse(control.id, "tabs-unavailable", "the browser did not list its tabs"),
            port,
          );
          return;
        }
        const listed = (Array.isArray(tabs) ? tabs : []).filter((tab) =>
          Number.isSafeInteger(tab?.id),
        );
        postNative(
          {
            protocol: PROTOCOL,
            version: VERSION,
            type: "response",
            id: control.id,
            ok: true,
            result: {
              kind: "tabs",
              tabs: listed.slice(0, MAX_LISTED_TABS).map((tab) => ({
                tab_id: tab.id,
                window_id: Number.isSafeInteger(tab.windowId) ? tab.windowId : null,
                index: Number.isSafeInteger(tab.index) ? tab.index : null,
                active: tab.active === true,
                hidden: tab.hidden === true,
                granted: state.grant?.tab_id === tab.id,
                ...(control.include_titles
                  ? { title: clippedText(tab.title), url: urlWithoutQuery(tab.url) }
                  : {}),
              })),
              total: listed.length,
              truncated: listed.length > MAX_LISTED_TABS,
              titles: control.include_titles ? "included-untrusted" : "omitted",
            },
          },
          port,
        );
        return;
      }
      if (control.operation === "release") {
        const grant = state.grant;
        if (!grant || (control.tab_id !== null && control.tab_id !== grant.tab_id)) {
          postNative(
            errorResponse(control.id, "tab-not-granted", "the tab has no active browser grant"),
            port,
          );
          return;
        }
        const revoked = copyGrant(grant);
        await revokeGrant("client-stop", true);
        reply(revoked, { revoked_grant: revoked });
        return;
      }
      const cursor = control.cursor ?? (await storedCursor());
      let started;
      if (control.tab_id === null) started = await startCurrent(null, control.capabilities, cursor);
      else {
        const tab = await api.tabs.get(control.tab_id).catch(() => null);
        started = await start(tab, control.capabilities, cursor);
      }
      if (!started.ok) {
        postNative(errorResponse(control.id, started.error.code, started.error.message), port);
        return;
      }
      reply(started.grant, { grant: started.grant, native: started.native });
    }
    async function handleNativeMessage(message, port = state.nativePort) {
      if (port !== state.nativePort) return;
      if (message?.type === "control") {
        await handleNativeControl(message, port);
        return;
      }
      if (
        message?.type === "cancel" &&
        message.protocol === PROTOCOL &&
        message.version === VERSION
      ) {
        const job = state.jobs.get(message.id);
        if (job && message.session === job.request.session) {
          job.cancelled = true;
          try {
            await api.tabs.sendMessage(job.request.tab_id, {
              type: "cancel",
              session: message.session,
              id: message.id,
            });
          } catch (_) {}
        }
        return;
      }
      if (message?.type === "ready") {
        try {
          const ready = validateReady(message);
          state.nativeReady = true;
          state.nativeSocket = ready.socket;
          state.nativeError = null;
          postNative(helloMessage(), port);
        } catch (_) {
          state.nativeError = "native-protocol";
          try {
            port.disconnect();
          } catch (_) {}
        }
        return;
      }
      if (message?.type === "request") {
        await handleNativeRequest(message, port);
        return;
      }
      state.nativeError = "native-protocol";
      try {
        port.disconnect();
      } catch (_) {}
    }
    async function handleTabUpdated(tabId, changeInfo) {
      if (state.grant?.tab_id !== tabId && state.pendingStart?.tabId !== tabId) return false;
      if (
        changeInfo &&
        (Object.prototype.hasOwnProperty.call(changeInfo, "url") || changeInfo.status === "loading")
      ) {
        await revokeGrant("navigation", true);
        return true;
      }
      return false;
    }
    async function handleCollectorDisconnected(tabId) {
      if (state.grant?.tab_id !== tabId) return false;
      await revokeGrant("collector-disconnected", false);
      return true;
    }
    async function handleTabRemoved(tabId, reason = "tab-closed") {
      if (state.grant?.tab_id !== tabId && state.pendingStart?.tabId !== tabId) return false;
      await revokeGrant(reason, true);
      return true;
    }
    function status() {
      return {
        ok: true,
        active: Boolean(state.grant),
        grant: copyGrant(state.grant),
        native: state.nativePort ? (state.nativeReady ? "ready" : "starting") : "unavailable",
        native_error: state.nativeError,
        debugger: state.debug ? "attached" : "detached",
        debugger_detach_reason: state.debuggerDetachReason ?? null,
      };
    }
    async function handleUiMessage(message, sender) {
      if (!extensionSender(api, sender))
        return errorResponse(
          null,
          "untrusted-sender",
          "only extension controls may change a browser session",
        );
      if (!isRecord(message))
        return errorResponse(null, "invalid-ui-message", "unsupported extension control message");
      if (message.type === "status") return status();
      return errorResponse(null, "invalid-ui-message", "unsupported extension control message");
    }
    // Badge text is held by the browser, so a controller that starts after an
    // earlier worker stopped must clear whatever that worker last showed.
    void updateBadge();
    return {
      start,
      startCurrent,
      stop: () => revokeGrant("client-stop", true),
      connectNative,
      handleDebuggerEvent: recordDebuggerEvent,
      handleDebuggerDetach,
      handleNativeMessage,
      handleTabUpdated,
      handleTabRemoved,
      handleCollectorDisconnected,
      handleUiMessage,
      status,
    };
  }
  // Firefox resolves a listener's returned Promise as the reply. Chromium-family
  // browsers deliver nothing for it, so their entry asks for sendResponse.
  function install(api, options = {}) {
    const controller = createController(api, options);
    controller.connectNative();
    api.tabs.onActivated?.addListener(() => controller.connectNative());
    api.debugger?.onEvent?.addListener((source, method, params) =>
      controller.handleDebuggerEvent(source, method, params),
    );
    api.debugger?.onDetach?.addListener((source, reason) =>
      controller.handleDebuggerDetach(source, reason),
    );
    api.tabs.onUpdated.addListener((tabId, changeInfo) => {
      void controller.handleTabUpdated(tabId, changeInfo);
    });
    api.tabs.onDetached?.addListener((tabId) => {
      void controller.handleTabRemoved(tabId, "window-changed");
    });
    api.tabs.onRemoved.addListener((tabId) => {
      void controller.handleTabRemoved(tabId);
    });
    api.runtime.onMessage.addListener((message, sender, sendResponse) => {
      const reply = controller.handleUiMessage(message, sender);
      if (options.respond !== "callback") return reply;
      void reply.then(sendResponse);
      return true;
    });
    if (options.acceptCollectorPorts) {
      // An unreferenced receiving port can be collected, which closes the channel
      // under the collector; a port that does close ends the grant for its tab.
      const collectorPorts = new Set();
      api.runtime.onConnect?.addListener((port) => {
        if (port.name !== "cu-collector" || !Number.isSafeInteger(port.sender?.tab?.id)) {
          port.disconnect();
          return;
        }
        collectorPorts.add(port);
        port.postMessage({ type: "ack" });
        port.onDisconnect.addListener(() => {
          collectorPorts.delete(port);
          void controller.handleCollectorDisconnected(port.sender.tab.id);
        });
      });
    }
    return controller;
  }
  return {
    PROTOCOL,
    VERSION,
    CAPABILITIES,
    LIMITS,
    READ_OPERATIONS,
    ACTION_OPERATIONS,
    ValidationError,
    validatePayload,
    validateRequest,
    validateCollectorInfo,
    createController,
    install,
  };
});

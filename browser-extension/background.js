(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.CUReadExtension = api;
    if (root.browser) api.install(root.browser);
  }
})(globalThis, function () {
  "use strict";

  const PROTOCOL = "cu-browser-read";
  const VERSION = 1;
  const MAX_MESSAGE_BYTES = 1024 * 1024;
  const MAX_QUERY_CHARS = 256;
  const MAX_ROLE_CHARS = 64;
  const MAX_PAGE = 1000;
  const MAX_PAGE_SIZE = 100;
  const MAX_ELEMENTS = 128;
  const MAX_SESSION_CHARS = 128;
  const MAX_DOCUMENT_ID_CHARS = 128;
  const MAX_REQUEST_ID_CHARS = 128;
  const MAX_GRANTS = 16;
  const READ_OPERATIONS = new Set(["observe", "query"]);
  const OBSERVE_FIELDS = new Set(["include_values", "limit"]);
  const QUERY_FIELDS = new Set(["search", "role", "visible", "page", "page_size", "include_values"]);

  const LIMITS = Object.freeze({
    max_message_bytes: MAX_MESSAGE_BYTES,
    max_pending_requests: 64,
    max_query_chars: MAX_QUERY_CHARS,
    max_page: MAX_PAGE,
    max_page_size: MAX_PAGE_SIZE,
    max_elements: MAX_ELEMENTS,
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
    for (const key of required) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) {
        throw new ValidationError(`message is missing ${key}`);
      }
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
    const id = boundedString(message.id, "id", MAX_REQUEST_ID_CHARS);
    const operation = message.operation;
    if (!READ_OPERATIONS.has(operation)) throw new ValidationError("operation is not a supported passive read");
    const session = boundedString(message.session, "session", MAX_SESSION_CHARS);
    const tabId = boundedInteger(message.tab_id, "tab_id", 0x7fffffff, 1);
    const documentId = boundedString(message.document_id, "document_id", MAX_DOCUMENT_ID_CHARS);
    const generation = message.generation;
    if (generation !== null) boundedInteger(generation, "generation", Number.MAX_SAFE_INTEGER);
    return {
      protocol: PROTOCOL,
      version: VERSION,
      type: "request",
      id,
      operation,
      session,
      tab_id: tabId,
      document_id: documentId,
      generation,
      payload: validatePayload(operation, message.payload),
    };
  }

  function secureId(prefix) {
    if (!globalThis.crypto || typeof globalThis.crypto.randomUUID !== "function") {
      throw new Error("secure randomness is unavailable");
    }
    return `${prefix}-${globalThis.crypto.randomUUID()}`;
  }

  function validRequestId(value) {
    return typeof value === "string" && value.length > 0 && value.length <= MAX_REQUEST_ID_CHARS;
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

  function validateCollectorInfo(message, session) {
    const allowed = new Set(["protocol", "version", "type", "ok", "session", "document_id", "generation"]);
    exactKeys(message, new Set(["protocol", "version", "type", "ok", "session", "document_id", "generation"]), allowed);
    if (message.protocol !== PROTOCOL || message.version !== VERSION || message.type !== "started" || message.ok !== true) {
      throw new ValidationError("collector did not start a read session");
    }
    if (message.session !== session) throw new ValidationError("collector session did not match");
    boundedString(message.document_id, "document_id", MAX_DOCUMENT_ID_CHARS);
    boundedInteger(message.generation, "generation", Number.MAX_SAFE_INTEGER);
    return message;
  }

  function validateReady(message) {
    const allowed = new Set(["protocol", "version", "type", "helper_id", "socket", "limits"]);
    exactKeys(message, allowed, allowed);
    if (message.protocol !== PROTOCOL || message.version !== VERSION || message.type !== "ready") {
      throw new ValidationError("unsupported native helper ready message");
    }
    boundedString(message.helper_id, "helper_id", 128);
    boundedString(message.socket, "socket", 4096);
    if (!isRecord(message.limits) || Object.keys(message.limits).length !== Object.keys(LIMITS).length ||
        !Object.entries(LIMITS).every(([key, value]) => message.limits[key] === value)) {
      throw new ValidationError("native helper limits do not match");
    }
    return message;
  }

  function validateCollectorResponse(message) {
    const allowed = new Set(["protocol", "version", "type", "id", "session", "tab_id", "document_id", "ok", "result", "error"]);
    exactKeys(message, new Set(["protocol", "version", "type", "id", "session", "tab_id", "document_id", "ok"]), allowed);
    if (message.protocol !== PROTOCOL || message.version !== VERSION || message.type !== "response") {
      throw new ValidationError("collector response has an unsupported envelope");
    }
    boundedString(message.id, "response id", MAX_REQUEST_ID_CHARS);
    boundedString(message.session, "response session", MAX_SESSION_CHARS);
    boundedInteger(message.tab_id, "response tab_id", 0x7fffffff, 1);
    boundedString(message.document_id, "response document_id", MAX_DOCUMENT_ID_CHARS);
    boundedBoolean(message.ok, "response ok");
    if (message.ok && !isRecord(message.result)) throw new ValidationError("successful response has no result object");
    if (!message.ok) {
      const error = message.error;
      if (!isRecord(error)) throw new ValidationError("refused response has no error object");
      exactKeys(error, new Set(["code", "state", "message"]), new Set(["code", "state", "message"]));
      boundedString(error.code, "error code", 64);
      boundedString(error.state, "error state", 64);
      boundedString(error.message, "error message", 256);
    }
    return message;
  }

  function grantFor(tab, session, info) {
    return {
      session,
      tab_id: tab.id,
      window_id: tab.windowId,
      document_id: info.document_id,
      generation: info.generation,
      active: tab.active === true,
      hidden: tab.hidden === true,
    };
  }

  function copyGrant(grant) {
    return grant ? { ...grant } : null;
  }

  function extensionSender(api, sender) {
    return Boolean(sender && !sender.tab && sender.id === api.runtime.id);
  }

  function createController(api) {
    const state = {
      grant: null,
      pendingStart: null,
      session: null,
      nativePort: null,
      nativeReady: false,
      nativeSocket: null,
      nativeError: null,
    };

    async function updateBadge() {
      const text = state.grant ? "ON" : "";
      if (api.browserAction && api.browserAction.setBadgeText) {
        await api.browserAction.setBadgeText({ text });
      }
      if (state.grant && api.browserAction && api.browserAction.setBadgeBackgroundColor) {
        await api.browserAction.setBadgeBackgroundColor({ color: "#276749" });
      }
    }

    function postNative(message, port = state.nativePort) {
      if (!port) return false;
      try {
        port.postMessage(message);
        return true;
      } catch (error) {
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

    function connectNative() {
      if (!state.grant || state.nativePort) return;
      if (!api.runtime || typeof api.runtime.connectNative !== "function") {
        state.nativeError = "native-unavailable";
        return;
      }
      let port;
      try {
        port = api.runtime.connectNative("org.cu.read");
      } catch (error) {
        state.nativeError = "native-unavailable";
        return;
      }
      state.nativePort = port;
      state.nativeReady = false;
      state.nativeSocket = null;
      port.onMessage.addListener((message) => { void handleNativeMessage(message, port); });
      port.onDisconnect.addListener(() => {
        if (state.nativePort !== port) return;
        state.nativePort = null;
        state.nativeReady = false;
        state.nativeSocket = null;
        state.nativeError = "native-unavailable";
      });
      if (!postNative(helloMessage(), port)) {
        try { port.disconnect(); } catch (error) { /* The browser owns the failed port. */ }
      }
    }

    async function revokeGrant(reason, stopCollector) {
      const grant = state.grant;
      const port = state.nativePort;
      state.pendingStart = null;
      state.grant = null;
      state.session = null;
      if (!grant) {
        await updateBadge();
        return { ok: true, active: false };
      }
      if (stopCollector && api.tabs && api.tabs.sendMessage) {
        try {
          await api.tabs.sendMessage(grant.tab_id, { type: "stop", session: grant.session });
        } catch (error) {
          // A stopped or navigating tab has no collector to stop.
        }
      }
      if (port) postNative(revokeMessage(grant, reason), port);
      state.nativePort = null;
      state.nativeReady = false;
      state.nativeSocket = null;
      state.nativeError = null;
      if (port) {
        try { port.disconnect(); } catch (error) { /* The browser may have closed it already. */ }
      }
      await updateBadge();
      return { ok: true, active: false, reason };
    }

    async function start(tab) {
      if (!tab || !Number.isSafeInteger(tab.id) || tab.id < 1 || tab.active !== true) {
        return errorResponse(null, "not-current-tab", "read sessions start only for the current tab");
      }
      if (state.grant || state.pendingStart) return errorResponse(null, "session-active", "stop the current read session first");
      if (!api.scripting || typeof api.scripting.executeScript !== "function") {
        return errorResponse(null, "collector-unavailable", "Firefox scripting support is unavailable");
      }
      const session = secureId("session");
      const attempt = { tabId: tab.id };
      state.pendingStart = attempt;
      let info;
      try {
        await api.scripting.executeScript({ target: { tabId: tab.id }, files: ["collector.js"] });
        info = await api.tabs.sendMessage(tab.id, { type: "start", session });
        validateCollectorInfo(info, session);
      } catch (error) {
        if (state.pendingStart === attempt) state.pendingStart = null;
        await updateBadge();
        return errorResponse(null, "collector-unavailable", "the current tab cannot host a passive collector");
      }
      if (state.pendingStart !== attempt) {
        await api.tabs.sendMessage(tab.id, { type: "stop", session }).catch(() => {});
        return errorResponse(null, "activation-cancelled", "activation was stopped or the tab navigated");
      }
      state.pendingStart = null;
      state.session = session;
      state.grant = grantFor(tab, session, info);
      state.nativeError = null;
      await updateBadge();
      connectNative();
      return { ok: true, active: true, grant: copyGrant(state.grant), native: state.nativePort ? "starting" : "unavailable" };
    }

    async function startCurrent(tab) {
      if (tab) return start(tab);
      try {
        const tabs = await api.tabs.query({ active: true, currentWindow: true });
        return start(tabs[0]);
      } catch (error) {
        return errorResponse(null, "not-current-tab", "the current tab could not be identified");
      }
    }

    async function handleNativeRequest(message, port) {
      let request;
      try {
        request = validateRequest(message);
      } catch (error) {
        const id = message && message.id;
        if (validRequestId(id)) postNative(errorResponse(id, "invalid-request", "the read request was refused"), port);
        return;
      }
      const grant = state.grant;
      if (!grant || state.session !== request.session || grant.tab_id !== request.tab_id) {
        postNative(errorResponse(request.id, "tab-not-granted", "the tab has no active read grant"), port);
        return;
      }
      if (grant.document_id !== request.document_id) {
        postNative(errorResponse(request.id, "stale-document", "the document grant is stale"), port);
        return;
      }
      let collected;
      try {
        collected = await api.tabs.sendMessage(request.tab_id, { type: "read-request", request });
        validateCollectorResponse(collected);
      } catch (error) {
        postNative(errorResponse(request.id, "collector-unavailable", "the granted tab did not return a read result"), port);
        return;
      }
      if (collected.id !== request.id || collected.session !== request.session || collected.tab_id !== request.tab_id) {
        postNative(errorResponse(request.id, "mismatched-response", "the collector response did not match the request"), port);
        await revokeGrant("mismatched-response", false);
        return;
      }
      if (collected.document_id !== grant.document_id) {
        postNative(errorResponse(request.id, "stale-document", "the collector document is no longer granted"), port);
        await revokeGrant("document-changed", false);
        return;
      }
      if (!collected.ok) {
        postNative(collected, port);
        return;
      }
      let tab;
      try {
        tab = await api.tabs.get(request.tab_id);
      } catch (error) {
        postNative(errorResponse(request.id, "tab-closed", "the granted tab is no longer available"), port);
        await revokeGrant("tab-closed", false);
        return;
      }
      if (state.grant !== grant || state.nativePort !== port) return;
      const result = {
        ...collected.result,
        context: {
          source: "background",
          semantics: "background read; not visible-desktop screenshot identity",
          tab_id: tab.id,
          window_id: tab.windowId,
          tab_active: tab.active === true,
          tab_hidden: tab.hidden === true,
          session: request.session,
          document_id: request.document_id,
          css_rects: { space: "css-viewport", projection: "unprojectable" },
        },
      };
      const response = { ...collected, result };
      if (new TextEncoder().encode(JSON.stringify(response)).length > MAX_MESSAGE_BYTES) {
        postNative(errorResponse(request.id, "observation-too-large", "request a narrower query or smaller limit"), port);
        return;
      }
      state.grant.active = tab.active === true;
      state.grant.hidden = tab.hidden === true;
      if (Number.isSafeInteger(collected.result.generation)) {
        state.grant.generation = collected.result.generation;
        postNative({ protocol: PROTOCOL, version: VERSION, type: "grant", grant: copyGrant(state.grant) }, port);
      }
      postNative(response, port);
    }

    async function handleNativeMessage(message, port = state.nativePort) {
      if (port !== state.nativePort) return;
      if (message && message.type === "ready") {
        try {
          const ready = validateReady(message);
          state.nativeReady = true;
          state.nativeSocket = ready.socket;
          state.nativeError = null;
          postNative(helloMessage(), port);
        } catch (error) {
          state.nativeError = "native-protocol";
          try { port.disconnect(); } catch (disconnectError) { /* The port is already unusable. */ }
        }
        return;
      }
      if (message && message.type === "request") {
        await handleNativeRequest(message, port);
        return;
      }
      state.nativeError = "native-protocol";
      try { port.disconnect(); } catch (error) { /* The browser may have closed it already. */ }
    }

    async function handleTabUpdated(tabId, changeInfo) {
      if (state.grant?.tab_id !== tabId && state.pendingStart?.tabId !== tabId) return false;
      if (changeInfo && (Object.prototype.hasOwnProperty.call(changeInfo, "url") || changeInfo.status === "loading")) {
        await revokeGrant("navigation", false);
        return true;
      }
      return false;
    }

    async function handleTabRemoved(tabId) {
      if (state.grant?.tab_id !== tabId && state.pendingStart?.tabId !== tabId) return false;
      await revokeGrant("tab-closed", false);
      return true;
    }

    function status() {
      return {
        ok: true,
        active: Boolean(state.grant),
        grant: copyGrant(state.grant),
        native: state.nativePort ? (state.nativeReady ? "ready" : "starting") : "unavailable",
        native_error: state.nativeError,
      };
    }

    async function handleUiMessage(message, sender) {
      if (!extensionSender(api, sender)) return errorResponse(null, "untrusted-sender", "only extension controls may change a read session");
      if (!isRecord(message)) return errorResponse(null, "invalid-ui-message", "unsupported extension control message");
      if (message.type === "start-current") return startCurrent();
      if (message.type === "stop") return revokeGrant("user-stop", true);
      if (message.type === "status") return status();
      return errorResponse(null, "invalid-ui-message", "unsupported extension control message");
    }

    return {
      start,
      startCurrent,
      stop: () => revokeGrant("user-stop", true),
      handleNativeMessage,
      handleTabUpdated,
      handleTabRemoved,
      handleUiMessage,
      status,
    };
  }

  function install(api) {
    const controller = createController(api);
    api.browserAction.onClicked.addListener((tab) => { void controller.startCurrent(tab); });
    api.tabs.onUpdated.addListener((tabId, changeInfo) => { void controller.handleTabUpdated(tabId, changeInfo); });
    api.tabs.onRemoved.addListener((tabId) => { void controller.handleTabRemoved(tabId); });
    api.runtime.onMessage.addListener((message, sender) => controller.handleUiMessage(message, sender));
    return controller;
  }

  return {
    PROTOCOL,
    VERSION,
    LIMITS,
    READ_OPERATIONS,
    ValidationError,
    validatePayload,
    validateRequest,
    validateCollectorInfo,
    createController,
    install,
  };
});

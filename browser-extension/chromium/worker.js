// The Chromium-family service worker shares the Firefox controller. Chromium
// delivers no reply for a listener's returned Promise, so both the controller
// and the injected collector answer through sendResponse.
importScripts("background.js");
globalThis.CUBrowserExtension.install(chrome, {
  respond: "callback",
  collectorFiles: ["collector.js", "chromium-collector.js"],
});

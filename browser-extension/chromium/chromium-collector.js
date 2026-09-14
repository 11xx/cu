if (globalThis.CUBrowserCollector && !globalThis.CUBrowserCollectorInstance) {
  globalThis.CUBrowserCollectorInstance = globalThis.CUBrowserCollector.install(chrome, document, {
    respond: "callback",
    releaseWhenDisconnected: true,
  });
}

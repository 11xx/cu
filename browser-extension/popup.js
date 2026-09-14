const extension = globalThis.browser || globalThis.chrome;
const statusElement = document.getElementById("status");
const capabilitiesElement = document.getElementById("capabilities");
const cursorElement = document.getElementById("cursor");

function render(response) {
  capabilitiesElement.textContent = "Capabilities: none";
  if (!response || response.ok !== true) {
    statusElement.textContent = "Session status is unavailable.";
    return;
  }
  if (!response.active) {
    statusElement.textContent =
      response.native_error === "native-unavailable"
        ? "The CU native host is unavailable. Run cu web setup for this browser."
        : "No tab is bound. A CU session binds one with cu web grant.";
    return;
  }
  const capabilities = response.grant.capabilities || ["read"];
  statusElement.textContent = `A CU session holds tab ${response.grant.tab_id}.`;
  capabilitiesElement.textContent = `Capabilities: ${capabilities.join(", ")}.`;
}

async function refresh() {
  try {
    render(await extension.runtime.sendMessage({ type: "status" }));
  } catch (error) {
    statusElement.textContent = "Session status is unavailable.";
  }
}

void extension.storage.local.get({ cursor: true }).then((stored) => {
  cursorElement.checked = stored.cursor !== false;
});
cursorElement.addEventListener("change", () => {
  void extension.storage.local.set({ cursor: cursorElement.checked });
});

void refresh();
const statusTimer = setInterval(refresh, 500);
window.addEventListener("pagehide", () => clearInterval(statusTimer));

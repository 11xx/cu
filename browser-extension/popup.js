const extension = globalThis.browser || globalThis.chrome;
const statusElement = document.getElementById("status");
const capabilitiesElement = document.getElementById("capabilities");
const readButton = document.getElementById("start-read");
const interactionButton = document.getElementById("start-interaction");
const stopButton = document.getElementById("stop");

const messages = {
  "not-current-tab": "Choose the current tab before starting.",
  "session-active": "A browser session is already active.",
  "collector-unavailable": "This tab cannot host CU Browser.",
  "untrusted-sender": "The session control was refused.",
  "invalid-ui-message": "The session control was refused.",
};

function showError(response) {
  const code = response && response.error && response.error.code;
  statusElement.textContent = messages[code] || "The session control was refused.";
  readButton.disabled = false;
  interactionButton.disabled = false;
}

function render(response) {
  if (!response || response.ok !== true) {
    showError(response);
    return;
  }
  if (!response.active) {
    statusElement.textContent = "No tab is granted.";
    capabilitiesElement.textContent = "Capabilities: none";
    readButton.disabled = false;
    interactionButton.disabled = false;
    stopButton.disabled = true;
    return;
  }
  const capabilities = response.grant.capabilities || ["read"];
  statusElement.textContent = `CU Browser session for tab ${response.grant.tab_id}.`;
  capabilitiesElement.textContent = `Capabilities: ${capabilities.join(", ")}.`;
  readButton.disabled = true;
  interactionButton.disabled = true;
  stopButton.disabled = false;
}

async function start(type) {
  readButton.disabled = true;
  interactionButton.disabled = true;
  try {
    render(
      await extension.runtime.sendMessage({
        type,
        ...(type === "start-interaction"
          ? { cursor: document.getElementById("cursor").checked }
          : {}),
      }),
    );
  } catch (error) {
    statusElement.textContent = "The browser session could not be started.";
    readButton.disabled = false;
    interactionButton.disabled = false;
  }
}

async function refresh() {
  try {
    render(await extension.runtime.sendMessage({ type: "status" }));
  } catch (error) {
    statusElement.textContent = "Session status is unavailable.";
  }
}

readButton.addEventListener("click", () => {
  void start("start-read");
});
interactionButton.addEventListener("click", () => {
  void start("start-interaction");
});
stopButton.addEventListener("click", async () => {
  stopButton.disabled = true;
  try {
    render(await extension.runtime.sendMessage({ type: "stop" }));
  } catch (error) {
    statusElement.textContent = "The browser session could not be stopped.";
    stopButton.disabled = false;
  }
});

void refresh();
const statusTimer = setInterval(refresh, 500);
window.addEventListener("pagehide", () => clearInterval(statusTimer));

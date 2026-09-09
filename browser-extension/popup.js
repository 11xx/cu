const statusElement = document.getElementById("status");
const startButton = document.getElementById("start");
const stopButton = document.getElementById("stop");

const messages = {
  "not-current-tab": "Choose the current tab before starting.",
  "session-active": "A read session is already active.",
  "collector-unavailable": "This tab cannot host a passive collector.",
  "untrusted-sender": "The session control was refused.",
  "invalid-ui-message": "The session control was refused.",
};

function showError(response) {
  const code = response && response.error && response.error.code;
  statusElement.textContent = messages[code] || "The session control was refused.";
  startButton.disabled = false;
}

function render(response) {
  if (!response || response.ok !== true) {
    showError(response);
    return;
  }
  if (!response.active) {
    statusElement.textContent = "No tab is granted.";
    startButton.disabled = false;
    stopButton.disabled = true;
    return;
  }
  const native = response.native === "ready" ? " Native helper ready." : " Native helper unavailable or starting.";
  statusElement.textContent = `Read session for tab ${response.grant.tab_id}.` + native;
  startButton.disabled = true;
  stopButton.disabled = false;
}

async function refresh() {
  try {
    render(await browser.runtime.sendMessage({ type: "status" }));
  } catch (error) {
    statusElement.textContent = "Session status is unavailable.";
  }
}

startButton.addEventListener("click", async () => {
  startButton.disabled = true;
  try {
    render(await browser.runtime.sendMessage({ type: "start-current" }));
  } catch (error) {
    statusElement.textContent = "The session could not be started.";
    startButton.disabled = false;
  }
});

stopButton.addEventListener("click", async () => {
  stopButton.disabled = true;
  try {
    render(await browser.runtime.sendMessage({ type: "stop" }));
  } catch (error) {
    statusElement.textContent = "The session could not be stopped.";
    stopButton.disabled = false;
  }
});

void refresh();
const statusTimer = setInterval(refresh, 500);
window.addEventListener("pagehide", () => clearInterval(statusTimer));

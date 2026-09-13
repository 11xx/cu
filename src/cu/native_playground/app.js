(function (root) {
  "use strict";

  const stateApi = root.CUNativePlaygroundState;
  if (!stateApi) throw new Error("CU native playground state is unavailable");

  const {
    TASK_DEFINITIONS,
    completionFor,
    createRunState,
    reduce,
  } = stateApi;
  const document = root.document;
  const timers = new Map();
  let idCounter = 0;
  let state = createRunState({ run_id: makeId("run"), at: Date.now() });

  function makeId(prefix) {
    if (root.crypto && typeof root.crypto.randomUUID === "function") {
      return `${prefix}-${root.crypto.randomUUID()}`;
    }
    idCounter += 1;
    return `${prefix}-${idCounter}`;
  }

  function selectedTask() {
    return state.tasks[state.selected_task_id];
  }

  function clearTimer(taskId) {
    const timer = timers.get(taskId);
    if (timer !== undefined) root.clearTimeout(timer);
    timers.delete(taskId);
  }

  function clearTimers() {
    for (const taskId of timers.keys()) clearTimer(taskId);
  }

  function dispatch(type, taskId, values = {}) {
    state = reduce(state, {
      ...values,
      type,
      task_id: taskId,
      at: values.at === undefined ? Date.now() : values.at,
    });
    render();
  }

  function button(label, action, taskId, disabled = false) {
    const control = document.createElement("button");
    control.type = "button";
    control.textContent = label;
    control.dataset.action = action;
    control.dataset.taskId = taskId;
    control.disabled = disabled;
    return control;
  }

  function renderAttempts(list, task) {
    if (!task.attempts.length) {
      const empty = document.createElement("p");
      empty.textContent = "No terminal attempts yet.";
      list.append(empty);
      return;
    }
    for (const receipt of task.attempts) {
      const item = document.createElement("li");
      item.textContent = `${receipt.outcome} · ${receipt.attempt_id} · ${receipt.reason.code}`;
      const details = document.createElement("pre");
      details.textContent = JSON.stringify(receipt, null, 2);
      item.append(details);
      list.append(item);
    }
  }

  function renderTasks() {
    const list = document.querySelector("#task-list");
    list.replaceChildren();
    for (const definition of TASK_DEFINITIONS) {
      const task = state.tasks[definition.task_id];
      const card = document.createElement("article");
      card.className = "task-card";
      card.dataset.taskId = task.task_id;
      card.dataset.status = task.status;
      if (task.task_id === state.selected_task_id) card.classList.add("selected");

      const heading = document.createElement("h3");
      heading.textContent = `${task.title} · ${task.status}`;
      card.append(heading);

      const objective = document.createElement("p");
      objective.textContent = `Objective: ${task.objective}`;
      card.append(objective);

      const meta = document.createElement("p");
      meta.className = "task-meta";
      meta.textContent = `Scope: ${task.scope} Seed: ${task.seed} Version: ${task.task_version}`;
      card.append(meta);

      const select = button(
        task.task_id === state.selected_task_id ? "Selected" : "Select task",
        "select",
        task.task_id,
        task.task_id === state.selected_task_id,
      );
      card.append(select);

      if (task.status === "not-started") card.append(button("Start", "start", task.task_id));
      if (["PASS", "FAIL", "ABORTED"].includes(task.status)) {
        card.append(button("Retry", "retry", task.task_id));
      }
      if (task.status === "running") {
        card.append(button("Abort (user)", "abort-user", task.task_id));
        card.append(button("Cancel (runner)", "abort-runner", task.task_id));
      }
      card.append(button("Reset task", "reset-task", task.task_id));

      if (task.current_attempt) {
        const running = document.createElement("p");
        running.textContent = `Attempt ${task.current_attempt.attempt_id}; deadline ${task.current_attempt.deadline_at}`;
        card.append(running);
      }
      if (task.last_receipt) {
        const reason = document.createElement("p");
        reason.textContent = `Last result: ${task.last_receipt.outcome} — ${task.last_receipt.reason.message}`;
        card.append(reason);
      }

      const history = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `Attempt history (${task.attempts.length})`;
      history.append(summary);
      const items = document.createElement("ol");
      items.className = "attempt-history";
      renderAttempts(items, task);
      history.append(items);
      card.append(history);
      list.append(card);
    }
  }

  function renderCapabilities() {
    const list = document.querySelector("#capability-list");
    list.replaceChildren();
    for (const [name, capability] of Object.entries(state.capabilities)) {
      const item = document.createElement("li");
      item.textContent = `${name}: ${capability.state} — ${capability.reason}`;
      list.append(item);
    }
  }

  function renderInteraction() {
    const task = selectedTask();
    const running = task?.status === "running";
    const controls = {
      entry: document.querySelector("#entry"),
      notes: document.querySelector("#notes"),
      apply: document.querySelector("#apply"),
      delay: document.querySelector("#delay"),
      bottom: document.querySelector("#bottom"),
    };
    const taskState = task?.current_attempt?.state || task?.draft || {};
    controls.entry.value = taskState.entry ?? "";
    controls.notes.value = taskState.notes ?? "";
    controls.entry.disabled = !running || task.task_id !== "native-basics";
    controls.notes.disabled = !running || task.task_id !== "native-basics";
    controls.apply.disabled = !running || task.task_id !== "native-basics";
    controls.delay.disabled = !running || task.task_id !== "native-followup";
    controls.bottom.disabled = !running || task.task_id !== "native-followup";
    document.querySelector("#basics-controls").hidden = task.task_id !== "native-basics";
    document.querySelector("#followup-controls").hidden = task.task_id !== "native-followup";

    document.querySelector("#selected-objective").textContent = `Selected ${task.title}: ${task.objective}`;
    const status = document.querySelector("#interaction-status");
    if (task.status === "not-started") {
      status.textContent = "NOT STARTED — press Start in the task summary before interacting.";
    } else if (task.status === "running") {
      status.textContent = `RUNNING — attempt ${task.current_attempt.attempt_id}; terminal outcome is pending.`;
    } else {
      status.textContent = `${task.status} — ${task.last_receipt.reason.code}: ${task.last_receipt.reason.message}`;
    }

    const delayed = document.querySelector("#delayed");
    delayed.textContent = taskState.ready ? "Ready" : "Not ready";
    document.querySelector("#receipt").textContent = task.last_receipt
      ? JSON.stringify(task.last_receipt, null, 2)
      : "No terminal attempt yet.";
  }

  function render() {
    const completion = completionFor(state);
    document.querySelector("#suite-progress").textContent =
      `Suite progress: ${completion.passed}/${completion.total} PASS · first-attempt PASS ${completion.first_attempt_passed}/${completion.total}`;
    document.querySelector("#run-meta").textContent = `Run: ${state.run_id} · terminal attempts: ${completion.terminal_attempts}`;
    renderTasks();
    renderCapabilities();
    renderInteraction();
  }

  function scheduleReadiness() {
    const task = selectedTask();
    const taskId = task.task_id;
    const attemptId = task.current_attempt?.attempt_id;
    dispatch("READY_REQUEST", taskId, { attempt_id: attemptId });
    clearTimer(taskId);
    if (!attemptId) return;
    timers.set(taskId, root.setTimeout(() => {
      const current = state.tasks[taskId];
      if (current.status === "running" && current.current_attempt?.attempt_id === attemptId) {
        dispatch("READY", taskId, { attempt_id: attemptId });
      }
      timers.delete(taskId);
    }, 350));
  }

  document.querySelector("#task-list").addEventListener("click", (event) => {
    const control = event.target.closest("button[data-action]");
    if (!control) return;
    const taskId = control.dataset.taskId;
    const action = control.dataset.action;
    if (action === "select") return dispatch("SELECT_TASK", taskId);
    if (["reset-task", "retry", "abort-user", "abort-runner"].includes(action)) clearTimer(taskId);
    if (action === "start") return dispatch("START", taskId, { attempt_id: makeId("attempt") });
    if (action === "retry") return dispatch("RETRY", taskId, { attempt_id: makeId("attempt") });
    if (action === "reset-task") return dispatch("RESET_TASK", taskId);
    if (action === "abort-user") return dispatch("ABORT", taskId, { cause: "user" });
    if (action === "abort-runner") return dispatch("ABORT", taskId, { cause: "runner-cancelled" });
  });

  document.querySelector("#entry").addEventListener("input", (event) => {
    dispatch("ENTRY_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#notes").addEventListener("input", (event) => {
    dispatch("NOTES_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#apply").addEventListener("click", () => {
    const task = selectedTask();
    dispatch("APPLY", task.task_id, {
      entry: document.querySelector("#entry").value,
      notes: document.querySelector("#notes").value,
      attempt_id: task.current_attempt?.attempt_id,
    });
  });
  document.querySelector("#delay").addEventListener("click", scheduleReadiness);
  document.querySelector("#bottom").addEventListener("click", () => {
    const task = selectedTask();
    dispatch("BOTTOM", task.task_id, { attempt_id: task.current_attempt?.attempt_id });
  });
  document.querySelector("#pane").addEventListener("scroll", (event) => {
    document.querySelector("#scroll-state").textContent = `scroll=${event.target.scrollTop}`;
  });
  document.querySelector("#reset-run").addEventListener("click", () => {
    clearTimers();
    state = reduce(state, { type: "RESET_RUN", run_id: makeId("run"), at: Date.now() });
    render();
  });
  document.querySelector("#save-receipt").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: "application/json" });
    const url = root.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `cu-native-playground-${state.run_id}.json`;
    link.click();
    root.setTimeout(() => root.URL.revokeObjectURL(url), 1000);
  });

  root.setInterval(() => {
    const next = reduce(state, { type: "TICK", at: Date.now() });
    if (next !== state) {
      state = next;
      render();
    }
  }, 100);

  root.CUNativePlayground = {
    dispatch,
    getState: () => state,
    render,
  };
  render();
})(window);

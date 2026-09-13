(function (root) {
  "use strict";

  const stateApi = root.CUNativePlaygroundState;
  if (!stateApi) throw new Error("CU native playground state is unavailable");

  const { TASK_DEFINITIONS, completionFor, createRunState, reduce } = stateApi;
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

  function button(label, action, taskId, disabled = false, itemId = null) {
    const control = document.createElement("button");
    control.type = "button";
    control.textContent = label;
    control.dataset.action = action;
    control.dataset.taskId = taskId;
    if (itemId) control.dataset.itemId = itemId;
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
      meta.textContent = `Scope: ${task.scope} Seed: ${task.seed} Fixture: ${task.fixture_seed || "p01-fixed"} Version: ${task.task_version}`;
      card.append(meta);

      const rules = document.createElement("p");
      rules.className = "task-rules";
      rules.textContent = `Permitted: ${task.permitted_actions} Forbidden: ${task.forbidden_effects}`;
      card.append(rules);

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

  function renderDispatch(task, taskState, running) {
    const fixture = task.fixture;
    document.querySelector("#dispatch-brief").textContent =
      `Brief: destination ${fixture.destination}; quantity ${fixture.quantity}; notes “${fixture.notes}”. Submit once.`;
    const destination = document.querySelector("#dispatch-destination");
    const quantity = document.querySelector("#dispatch-quantity");
    const notes = document.querySelector("#dispatch-notes");
    destination.value = taskState.destination ?? "";
    quantity.value = taskState.quantity ?? "";
    notes.value = taskState.notes ?? "";
    destination.disabled = !running;
    quantity.disabled = !running;
    notes.disabled = !running;
    document.querySelector("#dispatch-submit").disabled = !running;
    const status = document.querySelector("#dispatch-receipt-status");
    if (taskState.dispatch_receipt) {
      status.textContent = `Visible dispatch receipt: ${taskState.dispatch_receipt.destination} · ${taskState.dispatch_receipt.quantity} units.`;
    } else if (taskState.dispatch_pending) {
      status.textContent = "Dispatch submitted once; waiting for its visible receipt.";
    } else {
      status.textContent = "No dispatch receipt yet; typing alone is pending.";
    }
  }

  function renderParcel(task, taskState, running) {
    document.querySelector("#parcel-brief").textContent =
      `Job: ${task.fixture.job_label} · visible job ID: ${task.fixture.job_id}. Start once, wait for Ready, then acknowledge this ID.`;
    document.querySelector("#parcel-start-job").disabled = !running;
    document.querySelector("#parcel-ready").textContent = taskState.ready ? "Ready" : "Not ready";
    const receiptId = document.querySelector("#parcel-receipt-id");
    receiptId.value = taskState.acknowledgement_input ?? "";
    receiptId.disabled = !running;
    document.querySelector("#parcel-ack").disabled = !running;
    const status = document.querySelector("#parcel-receipt-status");
    if (taskState.parcel_receipt) {
      status.textContent = `Receipt observed for job ${taskState.parcel_receipt.job_id}.`;
    } else if (taskState.job_starts) {
      status.textContent = `Job started once; acknowledgement count ${taskState.acknowledgement_count}.`;
    } else {
      status.textContent = "No job started; acknowledgement is not available yet.";
    }
  }

  function renderElevator(task, taskState, running) {
    const fixture = task.fixture;
    document.querySelector("#elevator-brief").textContent =
      `Target: INNER pane ${fixture.pane_id} · Bottom target ${fixture.target_id}. Outer scrolling is context; the target receipt is authoritative.`;
    const outer = document.querySelector("#elevator-outer");
    const outerContent = document.querySelector("#elevator-outer-content");
    const inner = document.querySelector("#archive-inner");
    const innerContent = document.querySelector("#archive-inner-content");
    const target = document.querySelector("#archive-bottom");
    outerContent.style.minHeight = `${fixture.outer_content_height}px`;
    inner.style.height = `${fixture.inner_viewport}px`;
    inner.dataset.paneId = fixture.pane_id;
    innerContent.style.minHeight = `${fixture.inner_depth + fixture.inner_viewport}px`;
    target.style.marginTop = `${fixture.inner_depth}px`;
    target.dataset.paneId = fixture.pane_id;
    target.dataset.targetId = fixture.target_id;
    outer.scrollTop = taskState.outer_scroll ?? 0;
    inner.scrollTop = taskState.inner_scroll ?? 0;
    target.disabled = !running;
    document.querySelector("#elevator-progress").textContent =
      `INNER progress clue: ${taskState.inner_progress}% · outer scroll: ${taskState.outer_scroll} · inner scroll: ${taskState.inner_scroll}`;
    document.querySelector("#elevator-target-status").textContent = taskState.target_receipt
      ?.reached
      ? `Target receipt: reached in ${taskState.target_receipt.pane_id}. Bottom is ready.`
      : "Target receipt: not reached. Scroll the INNER pane and observe again.";
  }

  function renderGallery(task, taskState, running) {
    const fixture = task.fixture;
    const names = fixture.required_ids.map(
      (itemId) => fixture.items.find((item) => item.item_id === itemId)?.title || itemId,
    );
    document.querySelector("#gallery-brief").textContent =
      `Required subset: ${names.join(", ")}. Group action: ${fixture.group_action}. Simulated failed item: ${
        fixture.items.find((item) => item.item_id === fixture.failed_id)?.title || fixture.failed_id
      }.`;
    const selection = document.querySelector("#gallery-selection");
    const selectedNames = taskState.selected_ids.map(
      (itemId) => fixture.items.find((item) => item.item_id === itemId)?.title || itemId,
    );
    selection.textContent = `Selected ${taskState.selected_ids.length}/${fixture.required_ids.length}: ${
      selectedNames.join(", ") || "none"
    }`;

    const list = document.querySelector("#gallery-items");
    list.replaceChildren();
    for (const itemState of taskState.items) {
      const item = document.createElement("article");
      item.className = "gallery-item";
      item.dataset.itemId = itemState.item_id;
      item.dataset.status = itemState.status;

      const heading = document.createElement("h4");
      heading.textContent = `${itemState.title} · ${itemState.status}`;
      item.append(heading);
      const detail = document.createElement("p");
      detail.textContent = `Category: ${itemState.detail}`;
      item.append(detail);

      const selector = document.createElement("label");
      selector.className = "gallery-selector";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.action = "gallery-select";
      checkbox.dataset.taskId = task.task_id;
      checkbox.dataset.itemId = itemState.item_id;
      checkbox.checked = taskState.selected_ids.includes(itemState.item_id);
      checkbox.disabled = !running;
      selector.append(checkbox, " Select for group action");
      item.append(selector);

      const hoverControls = document.createElement("div");
      hoverControls.className = "gallery-hover-controls";
      hoverControls.setAttribute("aria-label", "Native hover controls");
      const hoverControl = button(
        "Inspect revealed controls",
        "gallery-hover-control",
        task.task_id,
        !running,
        itemState.item_id,
      );
      hoverControls.append(hoverControl);
      if (itemState.status === "simulated-failed") {
        hoverControls.append(
          button(
            "Recover this failed item",
            "gallery-recover",
            task.task_id,
            !running,
            itemState.item_id,
          ),
        );
      }
      item.append(hoverControls);
      item.addEventListener("mouseenter", () => {
        if (state.tasks[task.task_id].status === "running") {
          dispatch("GALLERY_HOVER", task.task_id, {
            item_id: itemState.item_id,
            attempt_id: state.tasks[task.task_id].current_attempt?.attempt_id,
          });
        }
      });
      list.append(item);
    }
    const group = document.querySelector("#gallery-group-action");
    group.textContent = fixture.group_action;
    group.disabled = !running;
    const status = document.querySelector("#gallery-action-status");
    if (taskState.group_action_submissions) {
      const succeeded = taskState.succeeded_ids.join(", ") || "none";
      status.textContent = `Group result: successes preserved (${succeeded}); simulated failure ${taskState.simulated_failed_id}. Recover only that item.`;
    } else {
      status.textContent =
        "No group action yet. Hover a representative card to reveal native controls.";
    }
  }

  function renderInteraction() {
    const task = selectedTask();
    const running = task?.status === "running";
    const taskState = task?.current_attempt?.state || task?.draft || {};
    const sections = {
      "basics-controls": "native-basics",
      "followup-controls": "native-followup",
      "dispatch-controls": "dispatch-desk",
      "parcel-controls": "delayed-parcel",
      "elevator-controls": "archive-elevator",
      "gallery-controls": "gallery-crew",
    };
    for (const [sectionId, taskId] of Object.entries(sections)) {
      document.querySelector(`#${sectionId}`).hidden = task.task_id !== taskId;
    }

    const entry = document.querySelector("#entry");
    const notes = document.querySelector("#notes");
    const apply = document.querySelector("#apply");
    const delay = document.querySelector("#delay");
    const bottom = document.querySelector("#bottom");
    entry.value = taskState.entry ?? "";
    notes.value = taskState.notes ?? "";
    entry.disabled = !running || task.task_id !== "native-basics";
    notes.disabled = !running || task.task_id !== "native-basics";
    apply.disabled = !running || task.task_id !== "native-basics";
    delay.disabled = !running || task.task_id !== "native-followup";
    bottom.disabled = !running || task.task_id !== "native-followup";

    document.querySelector("#selected-objective").textContent =
      `Selected ${task.title}: ${task.objective}`;
    const status = document.querySelector("#interaction-status");
    if (task.status === "not-started") {
      status.textContent = "NOT STARTED — press Start in the task summary before interacting.";
    } else if (task.status === "running") {
      status.textContent = `RUNNING — attempt ${task.current_attempt.attempt_id}; terminal outcome is pending.`;
    } else {
      status.textContent = `${task.status} — ${task.last_receipt.reason.code}: ${task.last_receipt.reason.message}`;
    }

    document.querySelector("#delayed").textContent = taskState.ready ? "Ready" : "Not ready";
    if (task.task_id === "dispatch-desk") renderDispatch(task, taskState, running);
    if (task.task_id === "delayed-parcel") renderParcel(task, taskState, running);
    if (task.task_id === "archive-elevator") renderElevator(task, taskState, running);
    if (task.task_id === "gallery-crew") renderGallery(task, taskState, running);
    document.querySelector("#receipt").textContent = task.last_receipt
      ? JSON.stringify(task.last_receipt, null, 2)
      : "No terminal attempt yet.";
  }

  function render() {
    const completion = completionFor(state);
    document.querySelector("#suite-progress").textContent =
      `Suite progress: ${completion.passed}/${completion.total} PASS · first-attempt PASS ${completion.first_attempt_passed}/${completion.total}`;
    document.querySelector("#run-meta").textContent =
      `Run: ${state.run_id} · suite seed: ${state.suite_seed} · terminal attempts: ${completion.terminal_attempts}`;
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
    timers.set(
      taskId,
      root.setTimeout(() => {
        const current = state.tasks[taskId];
        if (current.status === "running" && current.current_attempt?.attempt_id === attemptId) {
          dispatch("READY", taskId, { attempt_id: attemptId });
        }
        timers.delete(taskId);
      }, 350),
    );
  }

  function scheduleDispatchReceipt(taskId, attemptId) {
    clearTimer(taskId);
    timers.set(
      taskId,
      root.setTimeout(() => {
        const current = state.tasks[taskId];
        if (current.status === "running" && current.current_attempt?.attempt_id === attemptId) {
          dispatch("DISPATCH_RECEIPT", taskId, { attempt_id: attemptId });
        }
        timers.delete(taskId);
      }, 0),
    );
  }

  function scheduleParcelReady(taskId, attemptId) {
    const task = state.tasks[taskId];
    clearTimer(taskId);
    timers.set(
      taskId,
      root.setTimeout(() => {
        const current = state.tasks[taskId];
        if (current.status === "running" && current.current_attempt?.attempt_id === attemptId) {
          dispatch("PARCEL_READY", taskId, {
            attempt_id: attemptId,
            job_id: current.current_attempt.state.job_id,
          });
        }
        timers.delete(taskId);
      }, task.fixture.ready_delay_ms),
    );
  }

  function elevatorVisibility() {
    const task = selectedTask();
    if (task.task_id !== "archive-elevator" || task.status !== "running") return;
    const pane = document.querySelector("#archive-inner");
    const target = document.querySelector("#archive-bottom");
    const paneRect = pane.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const hasLayout = pane.clientHeight > 0 && targetRect.height > 0;
    const targetTop = target.offsetTop || Number.parseFloat(target.style.marginTop) || 0;
    const targetHeight = target.offsetHeight || 32;
    const viewportHeight = pane.clientHeight || task.fixture.inner_viewport;
    const visible = hasLayout
      ? targetRect.top >= paneRect.top &&
        targetRect.bottom <= paneRect.bottom &&
        targetRect.left >= paneRect.left &&
        targetRect.right <= paneRect.right
      : targetTop >= pane.scrollTop && targetTop + targetHeight <= pane.scrollTop + viewportHeight;
    const current = task.current_attempt.state;
    dispatch("ELEVATOR_TARGET_VISIBLE", task.task_id, {
      attempt_id: task.current_attempt.attempt_id,
      pane_id: pane.dataset.paneId,
      target_id: target.dataset.targetId,
      visible,
      progress: current.inner_progress,
    });
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

  document.querySelector("#dispatch-destination").addEventListener("input", (event) => {
    dispatch("DISPATCH_DESTINATION_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#dispatch-quantity").addEventListener("input", (event) => {
    dispatch("DISPATCH_QUANTITY_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#dispatch-notes").addEventListener("input", (event) => {
    dispatch("DISPATCH_NOTES_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#dispatch-submit").addEventListener("click", () => {
    const task = selectedTask();
    const attemptId = task.current_attempt?.attempt_id;
    dispatch("DISPATCH_SUBMIT", task.task_id, { attempt_id: attemptId });
    const current = state.tasks[task.task_id];
    if (current.status === "running" && current.current_attempt?.state.dispatch_pending) {
      scheduleDispatchReceipt(task.task_id, attemptId);
    }
  });

  document.querySelector("#parcel-receipt-id").addEventListener("input", (event) => {
    dispatch("PARCEL_RECEIPT_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#parcel-start-job").addEventListener("click", () => {
    const task = selectedTask();
    const attemptId = task.current_attempt?.attempt_id;
    dispatch("PARCEL_START_JOB", task.task_id, { attempt_id: attemptId });
    const current = state.tasks[task.task_id];
    if (current.status === "running" && current.current_attempt?.state.job_starts === 1) {
      scheduleParcelReady(task.task_id, attemptId);
    }
  });
  document.querySelector("#parcel-ack").addEventListener("click", () => {
    const task = selectedTask();
    dispatch("PARCEL_ACK", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      job_id: document.querySelector("#parcel-receipt-id").value,
    });
  });

  document.querySelector("#elevator-outer").addEventListener("scroll", (event) => {
    const task = selectedTask();
    dispatch("ELEVATOR_OUTER_SCROLL", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      scroll_top: event.target.scrollTop,
    });
    elevatorVisibility();
  });
  document.querySelector("#archive-inner").addEventListener("scroll", (event) => {
    const task = selectedTask();
    dispatch("ELEVATOR_INNER_SCROLL", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      pane_id: event.target.dataset.paneId,
      scroll_top: event.target.scrollTop,
    });
    elevatorVisibility();
  });
  document.querySelector("#archive-bottom").addEventListener("click", (event) => {
    const task = selectedTask();
    dispatch("ELEVATOR_BOTTOM", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      pane_id: event.currentTarget.dataset.paneId,
      target_id: event.currentTarget.dataset.targetId,
    });
  });

  document.querySelector("#gallery-items").addEventListener("change", (event) => {
    const control = event.target.closest('input[data-action="gallery-select"]');
    if (!control) return;
    const task = selectedTask();
    dispatch("GALLERY_SELECT", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      item_id: control.dataset.itemId,
    });
  });
  document.querySelector("#gallery-items").addEventListener("click", (event) => {
    const control = event.target.closest("button[data-action]");
    if (!control) return;
    const task = selectedTask();
    if (control.dataset.action === "gallery-hover-control") {
      return dispatch("GALLERY_HOVER_CONTROL", task.task_id, {
        attempt_id: task.current_attempt?.attempt_id,
        item_id: control.dataset.itemId,
      });
    }
    if (control.dataset.action === "gallery-recover") {
      return dispatch("GALLERY_RECOVER", task.task_id, {
        attempt_id: task.current_attempt?.attempt_id,
        item_id: control.dataset.itemId,
        item_ids: [control.dataset.itemId],
      });
    }
  });
  document.querySelector("#gallery-group-action").addEventListener("click", () => {
    const task = selectedTask();
    dispatch("GALLERY_GROUP_ACTION", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      selected_ids: [...(task.current_attempt?.state.selected_ids || [])],
    });
  });

  document.querySelector("#reset-run").addEventListener("click", () => {
    clearTimers();
    state = reduce(state, {
      type: "RESET_RUN",
      run_id: makeId("run"),
      suite_seed: state.suite_seed,
      at: Date.now(),
    });
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

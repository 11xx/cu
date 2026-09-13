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

  function renderQueue(task, taskState, running) {
    const fixture = task.fixture;
    document.querySelector("#queue-brief").textContent =
      `Play “${fixture.target_title}” by ${fixture.target_artist} FROM THE CURRENT QUEUE. Preserve queue order; the broader catalog contains same-title objects for contrast.`;
    const player = taskState.queue.find((item) => item.id === taskState.current_track_id);
    document.querySelector("#queue-player-status").textContent = player
      ? `Mock playback: ${player.title} · ${player.artist} · queue position ${taskState.current_index + 1}/${taskState.queue.length} · advances ${taskState.playback_advances}`
      : "Mock playback: no current object.";

    const currentList = document.querySelector("#queue-current-list");
    currentList.replaceChildren();
    taskState.queue.forEach((item, index) => {
      const row = document.createElement("li");
      row.dataset.objectId = item.id;
      row.textContent = `${item.title} · ${item.artist} · object ${item.id}`;
      const play = button("Play this queued object", "queue-play", task.task_id, !running, item.id);
      play.dataset.source = "queue";
      row.append(" ", play);
      if (index === taskState.current_index) row.classList.add("current-queue-item");
      currentList.append(row);
    });

    const catalogList = document.querySelector("#queue-catalog-list");
    catalogList.replaceChildren();
    taskState.catalog.forEach((item) => {
      const row = document.createElement("p");
      row.dataset.objectId = item.id;
      row.textContent = `${item.title} · ${item.artist} · object ${item.id}`;
      const play = button(
        "Play catalog object (replaces queue)",
        "queue-play",
        task.task_id,
        !running,
        item.id,
      );
      play.dataset.source = "catalog";
      row.append(" ", play);
      catalogList.append(row);
    });

    const queueIds = taskState.queue.map((item) => item.id).join(" → ");
    document.querySelector("#queue-order-status").textContent =
      `Current queue order: ${queueIds}. ${taskState.queue_replaced ? "The queue was replaced by a catalog object." : "Queue replacement has not occurred."}`;
  }

  function ledgerRows(taskState) {
    const filtered = taskState.rows.filter(
      (row) => taskState.view_filter === "all" || row.status === taskState.view_filter,
    );
    const direction = taskState.view_sort_direction === "desc" ? -1 : 1;
    return filtered.sort((left, right) => {
      const leftValue = left[taskState.view_sort_key];
      const rightValue = right[taskState.view_sort_key];
      const leftText = String(leftValue);
      const rightText = String(rightValue);
      const comparison =
        typeof leftValue === "number" && typeof rightValue === "number"
          ? leftValue - rightValue
          : leftText.localeCompare(rightText);
      return comparison === 0 ? left.id.localeCompare(right.id) : comparison * direction;
    });
  }

  function renderLedger(task, taskState, running) {
    const fixture = task.fixture;
    document.querySelector("#ledger-brief").textContent =
      `Required view: status “${fixture.filter_value}”, sort ${fixture.sort_key} ${fixture.sort_direction}. Inspect every matching row, then enter IDs ${fixture.matching_ids.join(", ")} and total ${fixture.expected_total}.`;
    document.querySelector("#ledger-filter").value = taskState.view_filter;
    document.querySelector("#ledger-sort").value = taskState.view_sort_key;
    document.querySelector("#ledger-direction").value = taskState.view_sort_direction;
    document.querySelector("#ledger-filter").disabled = !running;
    document.querySelector("#ledger-sort").disabled = !running;
    document.querySelector("#ledger-direction").disabled = !running;
    document.querySelector("#ledger-apply-view").disabled = !running;
    document.querySelector("#ledger-view-status").textContent =
      `Visible view: ${taskState.view_filter} · ${taskState.view_sort_key} ${taskState.view_sort_direction} · view changes ${taskState.view_changes}`;

    const rows = document.querySelector("#ledger-rows");
    rows.replaceChildren();
    for (const rowState of ledgerRows(taskState)) {
      const row = document.createElement("tr");
      row.dataset.rowId = rowState.id;
      for (const value of [
        rowState.id,
        rowState.status,
        rowState.amount,
        rowState.sender,
        rowState.note,
      ]) {
        const cell = document.createElement("td");
        cell.textContent = String(value);
        row.append(cell);
      }
      const coverage = document.createElement("td");
      const inspect = button(
        taskState.inspected_ids.includes(rowState.id) ? "Inspected" : "Inspect row",
        "ledger-inspect",
        task.task_id,
        !running || taskState.inspected_ids.includes(rowState.id),
        rowState.id,
      );
      coverage.append(inspect);
      row.append(coverage);
      rows.append(row);
    }
    document.querySelector("#ledger-search").value = taskState.search_query || "";
    document.querySelector("#ledger-search").disabled = !running;
    document.querySelector("#ledger-search-button").disabled = !running;
    document.querySelector("#ledger-search-status").textContent = taskState.search_result
      ? `Target search ${taskState.search_result.found ? "found" : "did not find"} ${taskState.search_result.row_id}; exhaustive coverage unchanged.`
      : `Known-row search is optional contrast evidence; it does not inspect the matching set.`;
    document.querySelector("#ledger-ids").value =
      taskState.submitted_ids_input ?? taskState.submitted_ids.join(", ");
    document.querySelector("#ledger-total").value =
      taskState.submitted_total_input ?? taskState.submitted_total;
    document.querySelector("#ledger-ids").disabled = !running;
    document.querySelector("#ledger-total").disabled = !running;
    document.querySelector("#ledger-submit").disabled = !running;
    document.querySelector("#ledger-answer-status").textContent =
      `Inspected ${taskState.inspected_ids.length}/${taskState.expected_ids.length} required rows; search results do not count.`;
  }

  function renderRecovery(task, taskState, running) {
    const fixture = task.fixture;
    document.querySelector("#recovery-brief").textContent =
      `Job: ${fixture.job_label}. Submit name “${fixture.draft_name}” with data “${fixture.payload}”. If the labeled in-page conflict appears, rename to “${fixture.rename_to}” and resume only the unfinished step.`;
    document.querySelector("#recovery-name").value = taskState.name ?? "";
    document.querySelector("#recovery-payload").value = taskState.payload ?? "";
    document.querySelector("#recovery-rename").value = taskState.rename_input ?? "";
    document.querySelector("#recovery-name").disabled = !running || taskState.job_submissions > 0;
    document.querySelector("#recovery-payload").disabled =
      !running || taskState.job_submissions > 0;
    document.querySelector("#recovery-submit").disabled = !running || taskState.job_submissions > 0;
    const dialog = document.querySelector("#recovery-conflict-dialog");
    dialog.hidden = !taskState.conflict_visible;
    document.querySelector("#recovery-conflict-text").textContent =
      `In-page conflict: “${fixture.existing_name}” already exists with data “${fixture.existing_payload}”. This dialog is part of the page; no OS chooser is involved. Preserve the submitted data, enter “${fixture.rename_to}”, confirm once, then resume.`;
    document.querySelector("#recovery-rename").disabled = !running || !taskState.conflict_visible;
    document.querySelector("#recovery-confirm").disabled = !running || !taskState.conflict_visible;
    document.querySelector("#recovery-resume").disabled = !running || !taskState.conflict_resolved;
    document.querySelector("#recovery-status").textContent = taskState.conflict_visible
      ? `Conflict visible; unfinished step: ${taskState.unfinished_step}. Do not resubmit.`
      : taskState.conflict_resolved
        ? `Conflict resolved; unfinished step: ${taskState.unfinished_step}. Resume once.`
        : taskState.job_submissions
          ? `Job submitted once; waiting for its in-page conflict.`
          : "No job submission yet; enter the specified data and submit once.";
  }

  function renderChanged(task, taskState, running) {
    const fixture = task.fixture;
    document.querySelector("#changed-brief").textContent =
      `Desk: ${fixture.desk_label}. Wait for the visible replacement, record a fresh observation, then confirm the intended control. Adjacent decoy is non-destructive but fails the attempt.`;
    document.querySelector("#changed-schedule").disabled =
      !running || taskState.update_requests > 0;
    document.querySelector("#changed-layout-status").textContent = taskState.layout_changed
      ? `Replacement visible: layout version ${taskState.layout_version}; current control is “${taskState.target_label}”. Fresh observations ${taskState.fresh_observations}.`
      : taskState.update_pending
        ? "Waiting for the delayed replacement; observe the visible desk when it changes."
        : `Old control visible: “${taskState.old_target_label}”. Request the delayed replacement first.`;
    const refresh = document.querySelector("#changed-refresh");
    refresh.disabled = !running || !taskState.layout_changed;
    refresh.dataset.targetId = taskState.layout_changed
      ? taskState.target_id
      : taskState.old_target_id;
    refresh.dataset.layoutVersion = String(taskState.layout_version);
    const confirm = document.querySelector("#changed-confirm");
    confirm.textContent = taskState.layout_changed
      ? taskState.target_label
      : taskState.old_target_label;
    confirm.disabled = !running;
    confirm.dataset.targetId = taskState.layout_changed
      ? taskState.target_id
      : taskState.old_target_id;
    confirm.dataset.layoutVersion = String(taskState.layout_version);
    const decoy = document.querySelector("#changed-decoy");
    decoy.textContent = fixture.decoy_label;
    decoy.disabled = !running;
    decoy.dataset.targetId = fixture.decoy_id;
    decoy.dataset.layoutVersion = String(taskState.layout_version);
    document.querySelector("#changed-action-status").textContent = taskState.decoy_effect
      ? `Decoy effect: ${taskState.decoy_effect}. Retry to attempt the intended target.`
      : `Observed layout version ${taskState.observed_layout_version || "none"}; intended target ${taskState.target_id}. The adjacent decoy produces no commit.`;
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
      "queue-controls": "queue-keeper",
      "ledger-controls": "receipt-ledger",
      "recovery-controls": "recovery-room",
      "changed-desk-controls": "changed-desk",
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
    if (task.task_id === "queue-keeper") renderQueue(task, taskState, running);
    if (task.task_id === "receipt-ledger") renderLedger(task, taskState, running);
    if (task.task_id === "recovery-room") renderRecovery(task, taskState, running);
    if (task.task_id === "changed-desk") renderChanged(task, taskState, running);
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

  function scheduleChangedLayout(taskId, attemptId) {
    const task = state.tasks[taskId];
    clearTimer(taskId);
    timers.set(
      taskId,
      root.setTimeout(() => {
        const current = state.tasks[taskId];
        if (current.status === "running" && current.current_attempt?.attempt_id === attemptId) {
          dispatch("CHANGED_LAYOUT", taskId, { attempt_id: attemptId });
        }
        timers.delete(taskId);
      }, task.fixture.update_delay_ms),
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

  document.querySelector("#queue-controls").addEventListener("click", (event) => {
    const control = event.target.closest('button[data-action="queue-play"]');
    if (!control) return;
    const task = selectedTask();
    dispatch("QUEUE_PLAY", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      source: control.dataset.source,
      item_id: control.dataset.itemId,
    });
  });

  document.querySelector("#ledger-apply-view").addEventListener("click", () => {
    const task = selectedTask();
    dispatch("LEDGER_VIEW", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      filter: document.querySelector("#ledger-filter").value,
      sort_key: document.querySelector("#ledger-sort").value,
      sort_direction: document.querySelector("#ledger-direction").value,
    });
  });
  document.querySelector("#ledger-rows").addEventListener("click", (event) => {
    const control = event.target.closest('button[data-action="ledger-inspect"]');
    if (!control) return;
    const task = selectedTask();
    dispatch("LEDGER_INSPECT", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      row_id: control.dataset.itemId,
    });
  });
  document.querySelector("#ledger-search").addEventListener("input", (event) => {
    dispatch("LEDGER_SEARCH_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#ledger-search-button").addEventListener("click", () => {
    const task = selectedTask();
    dispatch("LEDGER_SEARCH", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      row_id: document.querySelector("#ledger-search").value,
    });
  });
  document.querySelector("#ledger-ids").addEventListener("input", (event) => {
    dispatch("LEDGER_IDS_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#ledger-total").addEventListener("input", (event) => {
    dispatch("LEDGER_TOTAL_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#ledger-submit").addEventListener("click", () => {
    const task = selectedTask();
    const ids = document
      .querySelector("#ledger-ids")
      .value.split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    dispatch("LEDGER_SUBMIT", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      ids,
      total: document.querySelector("#ledger-total").value,
    });
  });

  document.querySelector("#recovery-name").addEventListener("input", (event) => {
    dispatch("RECOVERY_NAME_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#recovery-payload").addEventListener("input", (event) => {
    dispatch("RECOVERY_PAYLOAD_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#recovery-rename").addEventListener("input", (event) => {
    dispatch("RECOVERY_RENAME_INPUT", state.selected_task_id, { value: event.target.value });
  });
  document.querySelector("#recovery-submit").addEventListener("click", () => {
    const task = selectedTask();
    dispatch("RECOVERY_SUBMIT", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
    });
  });
  document.querySelector("#recovery-confirm").addEventListener("click", () => {
    const task = selectedTask();
    dispatch("RECOVERY_CONFIRM", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
    });
  });
  document.querySelector("#recovery-resume").addEventListener("click", () => {
    const task = selectedTask();
    dispatch("RECOVERY_RESUME", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
    });
  });

  document.querySelector("#changed-schedule").addEventListener("click", () => {
    const task = selectedTask();
    const attemptId = task.current_attempt?.attempt_id;
    dispatch("CHANGED_SCHEDULE", task.task_id, { attempt_id: attemptId });
    const current = state.tasks[task.task_id];
    if (current.status === "running" && current.current_attempt?.state.update_pending) {
      scheduleChangedLayout(task.task_id, attemptId);
    }
  });
  document.querySelector("#changed-refresh").addEventListener("click", (event) => {
    const task = selectedTask();
    dispatch("CHANGED_OBSERVE", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      target_id: event.currentTarget.dataset.targetId,
      layout_version: Number(event.currentTarget.dataset.layoutVersion),
    });
  });
  document.querySelector("#changed-confirm").addEventListener("click", (event) => {
    const task = selectedTask();
    dispatch("CHANGED_CONFIRM", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      target_id: event.currentTarget.dataset.targetId,
      layout_version: Number(event.currentTarget.dataset.layoutVersion),
    });
  });
  document.querySelector("#changed-decoy").addEventListener("click", (event) => {
    const task = selectedTask();
    dispatch("CHANGED_CONFIRM", task.task_id, {
      attempt_id: task.current_attempt?.attempt_id,
      target_id: event.currentTarget.dataset.targetId,
      layout_version: Number(event.currentTarget.dataset.layoutVersion),
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

(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.CUNativePlaygroundState = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  const SCHEMA = "cu-native-playground/1";
  const DEFAULT_TIMEOUT_MS = 5000;
  let generatedId = 0;

  const CAPABILITIES = Object.freeze({
    native_input: Object.freeze({ state: "available", reason: "ordinary browser input is in scope" }),
    extension_grant: Object.freeze({
      state: "unavailable",
      reason: "native tasks do not require an extension grant",
    }),
    real_file_output: Object.freeze({
      state: "unavailable",
      reason: "the foundation suite scores visible synthetic outcomes only",
    }),
  });

  function copy(value) {
    if (value === undefined) return undefined;
    if (Array.isArray(value)) return value.map(copy);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copy(item)]));
    }
    return value;
  }

  function freeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.values(value).forEach(freeze);
    return Object.freeze(value);
  }

  function id(prefix) {
    generatedId += 1;
    return `${prefix}-${generatedId}`;
  }

  function basicsDefinition() {
    return {
      task_id: "native-basics",
      task_version: 1,
      seed: "native-basics-seed-1",
      title: "Native basics",
      objective: "Replace both ordinary fields and press Apply once with the exact values.",
      scope: "The ordinary value and notes fields, plus the Apply button.",
      permitted_actions: "Edit the two fields and press Apply once.",
      forbidden_effects: "A wrong or repeated Apply is a failed committed answer.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: () => ({ entry: "8080", notes: "Original notes", applications: 0 }),
      evaluate(action, taskState) {
        if (action !== "apply") return null;
        const evidence = {
          entry: taskState.entry,
          notes: taskState.notes,
          applications: taskState.applications,
        };
        if (taskState.entry !== "4242" || taskState.notes !== "Luna was here") {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-submission",
              message: "Apply committed values that do not match the objective.",
            },
            evidence,
          };
        }
        if (taskState.applications !== 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "repeated-submission",
              message: "The objective permits exactly one Apply action.",
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: { code: "objective-reached", message: "The exact values were applied once." },
          evidence,
        };
      },
    };
  }

  function followupDefinition() {
    return {
      task_id: "native-followup",
      task_version: 1,
      seed: "native-followup-seed-1",
      title: "Readiness and scroll",
      objective: "Schedule readiness, wait for Ready, then press Bottom target once.",
      scope: "The readiness control, Ready signal and scroll-pane Bottom target.",
      permitted_actions: "Schedule readiness, wait for the visible Ready signal, scroll and press Bottom target once.",
      forbidden_effects: "Pressing Bottom target before Ready or more than once is a failed committed answer.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: () => ({ ready: false, readiness_requests: 0, bottom: 0 }),
      evaluate(action, taskState) {
        if (action !== "bottom") return null;
        const evidence = {
          ready: taskState.ready,
          readiness_requests: taskState.readiness_requests,
          bottom: taskState.bottom,
        };
        if (!taskState.ready) {
          return {
            outcome: "FAIL",
            reason: {
              code: "premature-bottom",
              message: "Bottom target was committed before the Ready signal.",
            },
            evidence,
          };
        }
        if (taskState.bottom !== 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "repeated-bottom",
              message: "The objective permits exactly one Bottom target action.",
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: { code: "objective-reached", message: "Ready preceded one Bottom target action." },
          evidence,
        };
      },
    };
  }

  const TASK_DEFINITIONS = Object.freeze([basicsDefinition(), followupDefinition()]);
  const DEFINITION_BY_ID = Object.freeze(
    Object.fromEntries(TASK_DEFINITIONS.map((definition) => [definition.task_id, definition])),
  );

  function taskRecord(definition) {
    return {
      task_id: definition.task_id,
      task_version: definition.task_version,
      seed: definition.seed,
      title: definition.title,
      objective: definition.objective,
      scope: definition.scope,
      permitted_actions: definition.permitted_actions,
      forbidden_effects: definition.forbidden_effects,
      timeout_ms: definition.timeout_ms,
      status: "not-started",
      draft: definition.initial_state(),
      current_attempt: null,
      attempts: [],
      last_receipt: null,
    };
  }

  function createRunState({ run_id = id("run"), at = 0 } = {}) {
    return freeze({
      schema: SCHEMA,
      run_id,
      created_at: at,
      selected_task_id: TASK_DEFINITIONS[0].task_id,
      capabilities: copy(CAPABILITIES),
      tasks: Object.fromEntries(TASK_DEFINITIONS.map((definition) => [definition.task_id, taskRecord(definition)])),
      run_history: [],
    });
  }

  function completionFor(state) {
    const tasks = Object.values(state.tasks);
    return {
      passed: tasks.filter((task) => task.attempts.some((attempt) => attempt.outcome === "PASS")).length,
      total: tasks.length,
      first_attempt_passed: tasks.filter((task) => task.attempts[0]?.outcome === "PASS").length,
      terminal_attempts: tasks.reduce((count, task) => count + task.attempts.length, 0),
    };
  }

  function replaceTask(state, taskId, update) {
    const oldTask = state.tasks[taskId];
    const editableTask = {
      ...oldTask,
      draft: copy(oldTask.draft),
      current_attempt: copy(oldTask.current_attempt),
      attempts: [...oldTask.attempts],
    };
    const nextTask = typeof update === "function" ? update(editableTask) : { ...editableTask, ...update };
    return freeze({ ...state, tasks: { ...state.tasks, [taskId]: nextTask } });
  }

  function activeAttempt(task, action) {
    if (task.status !== "running" || !task.current_attempt) return null;
    if (action.attempt_id && action.attempt_id !== task.current_attempt.attempt_id) return null;
    return task.current_attempt;
  }

  function beginAttempt(state, taskId, at, attemptId) {
    const definition = DEFINITION_BY_ID[taskId];
    return replaceTask(state, taskId, (task) => ({
      ...task,
      status: "running",
      draft: definition.initial_state(),
      current_attempt: {
        task_id: definition.task_id,
        task_version: definition.task_version,
        seed: definition.seed,
        attempt_id: attemptId || id("attempt"),
        started_at: at,
        deadline_at: at + definition.timeout_ms,
        event_count: 0,
        state: definition.initial_state(),
      },
    }));
  }

  function receiptFor(state, task, attempt, outcome, reason, evidence, at) {
    const elapsed = Math.max(0, at - attempt.started_at);
    return freeze({
      schema: SCHEMA,
      task_id: task.task_id,
      task_version: task.task_version,
      seed: task.seed,
      run_id: state.run_id,
      attempt_id: attempt.attempt_id,
      outcome,
      reason: copy(reason),
      evidence: copy(evidence),
      cost: {
        wall_time_ms: elapsed,
        controller_tokens: null,
        monetary: null,
      },
    });
  }

  function finish(state, taskId, outcome, reason, evidence, at) {
    return replaceTask(state, taskId, (task) => {
      const attempt = task.current_attempt;
      if (!attempt) return task;
      const receipt = receiptFor(state, task, attempt, outcome, reason, evidence, at);
      return {
        ...task,
        status: outcome,
        draft: copy(attempt.state),
        current_attempt: null,
        last_receipt: receipt,
        attempts: [...task.attempts, receipt],
      };
    });
  }

  function updateRunning(state, taskId, action, updateState, evaluationAction = null) {
    const task = state.tasks[taskId];
    const attempt = activeAttempt(task, action);
    if (!attempt) return state;
    const definition = DEFINITION_BY_ID[taskId];
    const nextAttempt = copy(attempt);
    nextAttempt.state = updateState(copy(nextAttempt.state));
    nextAttempt.event_count += 1;
    const updated = replaceTask(state, taskId, (current) => ({
      ...current,
      draft: copy(nextAttempt.state),
      current_attempt: nextAttempt,
    }));
    if (!evaluationAction) return updated;
    const result = definition.evaluate(evaluationAction, nextAttempt.state);
    return result ? finish(updated, taskId, result.outcome, result.reason, result.evidence, action.at) : updated;
  }

  function timeoutRunning(state, taskId, at) {
    const task = state.tasks[taskId];
    if (!task || task.status !== "running" || !task.current_attempt || at < task.current_attempt.deadline_at) {
      return state;
    }
    return finish(
      state,
      taskId,
      "FAIL",
      { code: "timeout", message: "The running attempt reached its deadline." },
      { deadline_at: task.current_attempt.deadline_at, observed_at: at, state: task.current_attempt.state },
      at,
    );
  }

  function reduce(state, action) {
    if (!action || typeof action.type !== "string") return state;
    const at = action.at === undefined ? 0 : action.at;
    if (action.type === "SELECT_TASK" && DEFINITION_BY_ID[action.task_id]) {
      return freeze({ ...copy(state), selected_task_id: action.task_id });
    }
    if (action.type === "RESET_RUN") {
      const fresh = createRunState({ run_id: action.run_id || id("run"), at });
      return freeze({
        ...fresh,
        run_history: [
          ...copy(state.run_history),
          { run_id: state.run_id, completion: completionFor(state), tasks: copy(state.tasks) },
        ],
      });
    }
    if (action.type === "TICK") {
      return Object.keys(state.tasks).reduce((next, taskId) => timeoutRunning(next, taskId, at), state);
    }
    const taskId = action.task_id || state.selected_task_id;
    if (!DEFINITION_BY_ID[taskId]) return state;
    const task = state.tasks[taskId];

    if (action.type === "START" && task.status === "not-started") {
      return beginAttempt(state, taskId, at, action.attempt_id);
    }
    if (action.type === "RETRY" && ["PASS", "FAIL", "ABORTED"].includes(task.status)) {
      return beginAttempt(state, taskId, at, action.attempt_id);
    }
    if (action.type === "RESET_TASK") {
      const definition = DEFINITION_BY_ID[taskId];
      return replaceTask(state, taskId, (current) => ({
        ...current,
        status: "not-started",
        draft: definition.initial_state(),
        current_attempt: null,
      }));
    }
    if (action.type === "ABORT") {
      const attempt = activeAttempt(task, action);
      if (!attempt) return state;
      const cause = action.cause === "runner-cancelled" ? "runner-cancelled" : "user";
      return finish(
        state,
        taskId,
        "ABORTED",
        {
          code: cause === "user" ? "user-abort" : "runner-cancellation",
          cause,
          message: cause === "user" ? "The user stopped the running attempt." : "The runner cancelled the attempt.",
        },
        { state: attempt.state },
        at,
      );
    }
    if (action.type === "FORBIDDEN_EFFECT") {
      const attempt = activeAttempt(task, action);
      if (!attempt) return state;
      return finish(
        state,
        taskId,
        "FAIL",
        { code: "forbidden-effect", message: "The attempt performed an explicitly forbidden effect." },
        { effect: action.effect || "unspecified", state: attempt.state },
        at,
      );
    }
    if (action.type === "ENTRY_INPUT" || action.type === "NOTES_INPUT") {
      const field = action.type === "ENTRY_INPUT" ? "entry" : "notes";
      if (["PASS", "FAIL", "ABORTED"].includes(task.status)) return state;
      const value = String(action.value ?? "");
      if (task.status === "running") {
        return updateRunning(state, taskId, action, (current) => ({ ...current, [field]: value }));
      }
      return replaceTask(state, taskId, (current) => ({
        ...current,
        draft: { ...current.draft, [field]: value },
      }));
    }
    if (action.type === "APPLY") {
      const entry = String(action.entry ?? task.current_attempt?.state.entry ?? task.draft.entry ?? "");
      const notes = String(action.notes ?? task.current_attempt?.state.notes ?? task.draft.notes ?? "");
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({ ...current, entry, notes, applications: current.applications + 1 }),
        "apply",
      );
    }
    if (action.type === "READY_REQUEST") {
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        ready: false,
        readiness_requests: current.readiness_requests + 1,
      }));
    }
    if (action.type === "READY") {
      return updateRunning(state, taskId, action, (current) => ({ ...current, ready: true }));
    }
    if (action.type === "BOTTOM") {
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({ ...current, bottom: current.bottom + 1 }),
        "bottom",
      );
    }
    return state;
  }

  return {
    SCHEMA,
    TASK_DEFINITIONS,
    createRunState,
    createInitialState: createRunState,
    completionFor,
    reduce,
  };
});

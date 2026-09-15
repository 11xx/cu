import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const require = createRequire(import.meta.url);
const stateApi = require("../src/cu/native_playground/state.js");
const assetRoot = new URL("../src/cu/native_playground/", import.meta.url);
const html = readFileSync(new URL("index.html", assetRoot), "utf8");
const stateSource = readFileSync(new URL("state.js", assetRoot), "utf8");
const appSource = readFileSync(new URL("app.js", assetRoot), "utf8");
const styleSource = readFileSync(new URL("style.css", assetRoot), "utf8");

function fixture() {
  const dom = new JSDOM(html, {
    runScripts: "dangerously",
    url: "http://127.0.0.1/",
  });
  dom.window.eval(stateSource);
  dom.window.eval(appSource);
  return dom;
}

function task(dom, taskId) {
  return dom.window.CUNativePlayground.getState().tasks[taskId];
}

function click(dom, action, taskId) {
  dom.window.document
    .querySelector(`button[data-action="${action}"][data-task-id="${taskId}"]`)
    .click();
}

function set(dom, id, value) {
  const field = dom.window.document.getElementById(id);
  field.value = value;
  field.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
}

async function waitFor(predicate, message) {
  const deadline = Date.now() + 1500;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail(message);
}

test("the pinned task summary holds no task cards or controls", () => {
  const dom = fixture();
  try {
    assert.match(styleSource, /\.task-summary\s*\{[^}]*position:\s*sticky/);
    const summary = dom.window.document.querySelector(".task-summary");
    assert.equal(summary.querySelector("#task-list, .task-card, button, details"), null);
    assert.ok(dom.window.document.querySelector(".task-catalog #task-list .task-card"));
  } finally {
    dom.window.close();
  }
});

test("native basics retain a failed first attempt before a passing retry", () => {
  const dom = fixture();
  try {
    const taskId = "native-basics";
    assert.equal(task(dom, taskId).status, "not-started");
    click(dom, "start", taskId);
    const firstAttempt = task(dom, taskId).current_attempt.attempt_id;
    dom.window.document.querySelector("#apply").click();
    const failed = task(dom, taskId).last_receipt;
    assert.equal(task(dom, taskId).status, "FAIL");
    assert.equal(failed.outcome, "FAIL");
    assert.equal(failed.reason.code, "wrong-submission");
    assert.equal(failed.task_id, taskId);
    assert.equal(failed.task_version, 1);
    assert.equal(failed.seed, "native-basics-seed-1");
    assert.equal(failed.run_id, dom.window.CUNativePlayground.getState().run_id);
    assert.equal(failed.attempt_id, firstAttempt);
    assert.equal(typeof failed.cost.wall_time_ms, "number");

    set(dom, "entry", "4242");
    set(dom, "notes", "Luna was here");
    dom.window.document.querySelector("#apply").click();
    assert.strictEqual(task(dom, taskId).last_receipt, failed);
    assert.equal(task(dom, taskId).attempts.length, 1);

    click(dom, "retry", taskId);
    const secondAttempt = task(dom, taskId).current_attempt.attempt_id;
    assert.notEqual(secondAttempt, firstAttempt);
    set(dom, "entry", "4242");
    set(dom, "notes", "Luna was here");
    dom.window.document.querySelector("#apply").click();
    assert.equal(task(dom, taskId).status, "PASS");
    assert.equal(task(dom, taskId).attempts.length, 2);
    assert.strictEqual(task(dom, taskId).attempts[0], failed);
    assert.equal(task(dom, taskId).attempts[1].outcome, "PASS");
    assert.equal(
      dom.window.document.querySelector("#suite-progress").textContent.includes("1/10 PASS"),
      true,
    );
  } finally {
    dom.window.close();
  }
});

test("follow-up state is independent and reaches PASS only after Ready", async () => {
  const dom = fixture();
  try {
    const taskId = "native-followup";
    click(dom, "select", taskId);
    click(dom, "start", taskId);
    dom.window.document.querySelector("#delay").click();
    await new Promise((resolve) => setTimeout(resolve, 400));
    assert.equal(task(dom, taskId).current_attempt.state.ready, true);
    dom.window.document.querySelector("#bottom").click();
    assert.equal(task(dom, taskId).status, "PASS");
    assert.equal(task(dom, "native-basics").attempts.length, 0);
  } finally {
    dom.window.close();
  }
});

test("the native page has no extension diagnostic controls or shared result counter", () => {
  const dom = fixture();
  try {
    const document = dom.window.document;
    for (const id of ["hover-action", "mutate", "bulk-download", "sensitive", "media"]) {
      assert.equal(document.getElementById(id), null, id);
    }
    const before = dom.window.CUNativePlayground.getState();
    const after = stateApi.reduce(before, { type: "DIAGNOSTIC", task_id: "native-basics", at: 1 });
    assert.strictEqual(after, before);
    assert.equal(task(dom, "native-basics").status, "not-started");
  } finally {
    dom.window.close();
  }
});

test("timeout and cancellation produce terminal receipts with distinct causes", () => {
  let state = stateApi.createRunState({ run_id: "run-timeout", at: 100 });
  state = stateApi.reduce(state, {
    type: "START",
    task_id: "native-basics",
    attempt_id: "attempt-timeout",
    at: 100,
  });
  state = stateApi.reduce(state, { type: "TICK", at: 5101 });
  assert.equal(state.tasks["native-basics"].status, "FAIL");
  assert.equal(state.tasks["native-basics"].last_receipt.reason.code, "timeout");
  const receipt = state.tasks["native-basics"].last_receipt;
  state = stateApi.reduce(state, {
    type: "ENTRY_INPUT",
    task_id: "native-basics",
    value: "ignored",
    at: 5200,
  });
  assert.strictEqual(state.tasks["native-basics"].last_receipt, receipt);

  state = stateApi.createRunState({ run_id: "run-abort", at: 0 });
  state = stateApi.reduce(state, {
    type: "START",
    task_id: "native-basics",
    attempt_id: "attempt-user",
    at: 0,
  });
  state = stateApi.reduce(state, {
    type: "ABORT",
    task_id: "native-basics",
    cause: "user",
    at: 10,
  });
  assert.equal(state.tasks["native-basics"].last_receipt.outcome, "ABORTED");
  assert.equal(state.tasks["native-basics"].last_receipt.reason.cause, "user");

  state = stateApi.reduce(state, {
    type: "RETRY",
    task_id: "native-basics",
    attempt_id: "attempt-runner",
    at: 20,
  });
  state = stateApi.reduce(state, {
    type: "ABORT",
    task_id: "native-basics",
    cause: "runner-cancelled",
    at: 30,
  });
  assert.equal(state.tasks["native-basics"].last_receipt.reason.cause, "runner-cancelled");
});

test("reset task, retry and reset run are distinct operations", () => {
  let state = stateApi.createRunState({ run_id: "run-one", at: 0 });
  state = stateApi.reduce(state, {
    type: "START",
    task_id: "native-basics",
    attempt_id: "attempt-one",
    at: 0,
  });
  state = stateApi.reduce(state, { type: "ABORT", task_id: "native-basics", cause: "user", at: 5 });
  state = stateApi.reduce(state, { type: "RESET_TASK", task_id: "native-basics", at: 6 });
  assert.equal(state.tasks["native-basics"].status, "not-started");
  assert.equal(state.tasks["native-basics"].attempts.length, 1);
  state = stateApi.reduce(state, {
    type: "START",
    task_id: "native-basics",
    attempt_id: "attempt-two",
    at: 7,
  });
  assert.equal(state.tasks["native-basics"].current_attempt.attempt_id, "attempt-two");
  state = stateApi.reduce(state, { type: "RESET_RUN", run_id: "run-two", at: 8 });
  assert.equal(state.run_id, "run-two");
  assert.equal(state.run_history.length, 1);
  assert.equal(state.tasks["native-basics"].status, "not-started");
  assert.equal(state.tasks["native-basics"].attempts.length, 0);
  assert.equal(state.capabilities.extension_grant.state, "unavailable");
  assert.equal(stateApi.completionFor(state).passed, 0);
});

test("phase-A tasks are seed-varied, replayable and visibly specified", () => {
  const first = stateApi.createRunState({ run_id: "run-alpha", suite_seed: "alpha" });
  const replay = stateApi.createRunState({ run_id: "run-alpha-replay", suite_seed: "alpha" });
  const other = stateApi.createRunState({ run_id: "run-beta", suite_seed: "beta" });
  assert.deepEqual(Object.keys(first.tasks), [
    "native-basics",
    "native-followup",
    "dispatch-desk",
    "delayed-parcel",
    "archive-elevator",
    "gallery-crew",
    "queue-keeper",
    "receipt-ledger",
    "recovery-room",
    "changed-desk",
  ]);
  for (const taskId of ["dispatch-desk", "delayed-parcel", "archive-elevator", "gallery-crew"]) {
    assert.equal(first.tasks[taskId].fixture_seed, replay.tasks[taskId].fixture_seed);
    assert.notEqual(first.tasks[taskId].fixture_seed, other.tasks[taskId].fixture_seed);
    assert.match(first.tasks[taskId].objective, /.+/);
    assert.match(first.tasks[taskId].permitted_actions, /.+/);
    assert.match(first.tasks[taskId].forbidden_effects, /.+/);
  }
  const dom = fixture();
  try {
    assert.equal(dom.window.document.querySelectorAll(".task-card").length, 10);
    assert.match(dom.window.document.querySelector("#task-list").textContent, /Dispatch desk/);
    assert.match(dom.window.document.querySelector("#task-list").textContent, /Forbidden:/);
  } finally {
    dom.window.close();
  }
});

test("phase-B fixtures are seed-varied, replayable and visibly specified", () => {
  const first = stateApi.createRunState({ run_id: "run-phase-b-alpha", suite_seed: "alpha" });
  const replay = stateApi.createRunState({ run_id: "run-phase-b-replay", suite_seed: "alpha" });
  const other = stateApi.createRunState({ run_id: "run-phase-b-beta", suite_seed: "beta" });
  const varied = ["alpha", "beta", "gamma", "native-playground-seed-1"].map((suite_seed) =>
    stateApi.createRunState({ suite_seed }),
  );
  for (const taskId of ["queue-keeper", "receipt-ledger", "recovery-room", "changed-desk"]) {
    assert.equal(first.tasks[taskId].fixture_seed, replay.tasks[taskId].fixture_seed);
    assert.notEqual(first.tasks[taskId].fixture_seed, other.tasks[taskId].fixture_seed);
    assert.ok(
      new Set(varied.map((state) => state.tasks[taskId].fixture.variant_index)).size > 1,
      `${taskId} should select more than one deterministic fixture variant`,
    );
    assert.match(first.tasks[taskId].objective, /.+/);
    assert.match(first.tasks[taskId].permitted_actions, /.+/);
    assert.match(first.tasks[taskId].forbidden_effects, /.+/);
  }
});

test("dispatch desk keeps typing pending, rejects duplicate submission, and accepts one receipt", async () => {
  const dom = fixture();
  try {
    const taskId = "dispatch-desk";
    click(dom, "select", taskId);
    click(dom, "start", taskId);
    const taskFixture = task(dom, taskId).fixture;
    dom.window.document.querySelector("#dispatch-submit").click();
    assert.equal(task(dom, taskId).status, "FAIL");
    assert.equal(task(dom, taskId).last_receipt.reason.code, "wrong-destination");

    click(dom, "retry", taskId);
    set(dom, "dispatch-destination", taskFixture.destination);
    set(dom, "dispatch-quantity", String(taskFixture.quantity));
    set(dom, "dispatch-notes", taskFixture.notes);
    assert.equal(task(dom, taskId).status, "running");
    assert.equal(task(dom, taskId).attempts.length, 1);
    assert.equal(task(dom, taskId).last_receipt.reason.code, "wrong-destination");

    dom.window.document.querySelector("#dispatch-submit").click();
    assert.equal(task(dom, taskId).status, "running");
    dom.window.document.querySelector("#dispatch-submit").click();
    assert.equal(task(dom, taskId).status, "FAIL");
    assert.equal(task(dom, taskId).last_receipt.reason.code, "duplicate-dispatch");

    click(dom, "retry", taskId);
    set(dom, "dispatch-destination", taskFixture.destination);
    set(dom, "dispatch-quantity", String(taskFixture.quantity));
    set(dom, "dispatch-notes", taskFixture.notes);
    dom.window.document.querySelector("#dispatch-submit").click();
    await waitFor(() => task(dom, taskId).status === "PASS", "dispatch receipt did not arrive");
    assert.equal(task(dom, taskId).attempts.length, 3);
    assert.equal(task(dom, taskId).attempts[2].outcome, "PASS");
    assert.equal(task(dom, taskId).attempts[2].evidence.dispatch_submissions, 1);
  } finally {
    dom.window.close();
  }
});

test("delayed parcel rejects wrong and early acknowledgements, then waits for visible Ready", async () => {
  const dom = fixture();
  try {
    const taskId = "delayed-parcel";
    click(dom, "select", taskId);
    click(dom, "start", taskId);
    set(dom, "parcel-receipt-id", "parcel-job-not-this-one");
    dom.window.document.querySelector("#parcel-start-job").click();
    dom.window.document.querySelector("#parcel-ack").click();
    assert.equal(task(dom, taskId).last_receipt.reason.code, "wrong-job");

    click(dom, "retry", taskId);
    const expectedJobId = task(dom, taskId).fixture.job_id;
    dom.window.document.querySelector("#parcel-start-job").click();
    set(dom, "parcel-receipt-id", expectedJobId);
    dom.window.document.querySelector("#parcel-ack").click();
    assert.equal(task(dom, taskId).last_receipt.reason.code, "parcel-not-ready");

    click(dom, "retry", taskId);
    dom.window.document.querySelector("#parcel-start-job").click();
    await waitFor(
      () => dom.window.document.querySelector("#parcel-ready").textContent === "Ready",
      "parcel never published visible Ready",
    );
    assert.equal(task(dom, taskId).current_attempt.state.ready, true);
    set(dom, "parcel-receipt-id", expectedJobId);
    dom.window.document.querySelector("#parcel-ack").click();
    assert.equal(task(dom, taskId).status, "PASS");
    assert.equal(task(dom, taskId).last_receipt.evidence.ready_job_id, expectedJobId);
  } finally {
    dom.window.close();
  }
});

test("archive elevator requires the observed INNER target rather than a numeric offset", () => {
  const dom = fixture();
  try {
    const taskId = "archive-elevator";
    click(dom, "select", taskId);
    click(dom, "start", taskId);
    const current = task(dom, taskId);
    const { pane_id: paneId, target_id: targetId, inner_depth: innerDepth } = current.fixture;
    const attemptId = current.current_attempt.attempt_id;
    dom.window.CUNativePlayground.dispatch("ELEVATOR_TARGET_VISIBLE", taskId, {
      attempt_id: attemptId,
      pane_id: "nearby-outer-pane",
      target_id: targetId,
      visible: true,
      progress: 100,
      at: 1,
    });
    dom.window.CUNativePlayground.dispatch("ELEVATOR_BOTTOM", taskId, {
      attempt_id: attemptId,
      pane_id: "nearby-outer-pane",
      target_id: targetId,
      at: 2,
    });
    assert.equal(task(dom, taskId).last_receipt.reason.code, "wrong-pane");

    click(dom, "retry", taskId);
    const offsetAttemptId = task(dom, taskId).current_attempt.attempt_id;
    dom.window.CUNativePlayground.dispatch("ELEVATOR_INNER_SCROLL", taskId, {
      attempt_id: offsetAttemptId,
      pane_id: paneId,
      scroll_top: innerDepth,
      at: 3,
    });
    dom.window.CUNativePlayground.dispatch("ELEVATOR_BOTTOM", taskId, {
      attempt_id: offsetAttemptId,
      pane_id: paneId,
      target_id: targetId,
      at: 4,
    });
    assert.equal(task(dom, taskId).last_receipt.reason.code, "target-not-reached");

    click(dom, "retry", taskId);
    const outer = dom.window.document.querySelector("#elevator-outer");
    outer.scrollTop = current.fixture.initial_outer_scroll + 35;
    outer.dispatchEvent(new dom.window.Event("scroll"));
    const inner = dom.window.document.querySelector("#archive-inner");
    inner.scrollTop = innerDepth;
    inner.dispatchEvent(new dom.window.Event("scroll"));
    assert.match(
      dom.window.document.querySelector("#elevator-target-status").textContent,
      /reached/,
    );
    dom.window.document.querySelector("#archive-bottom").click();
    assert.equal(task(dom, taskId).status, "PASS");
    assert.equal(task(dom, taskId).last_receipt.evidence.target_receipt.target_id, targetId);
    assert.equal(task(dom, taskId).last_receipt.evidence.bottom_clicks, 1);
  } finally {
    dom.window.close();
  }
});

test("gallery crew uses native CSS hover, exact membership, one group action, and targeted recovery", () => {
  const dom = fixture();
  try {
    const taskId = "gallery-crew";
    click(dom, "select", taskId);
    click(dom, "start", taskId);
    const taskFixture = task(dom, taskId).fixture;
    assert.match(styleSource, /\.gallery-item:hover \.gallery-hover-controls/);
    const representative = dom.window.document.querySelector(".gallery-item");
    assert.equal(
      representative.querySelector(".gallery-hover-controls").getAttribute("aria-label"),
      "Native hover controls",
    );
    representative.dispatchEvent(new dom.window.Event("mouseenter"));
    assert.equal(task(dom, taskId).current_attempt.state.hover_events, 1);

    for (const itemId of taskFixture.required_ids) {
      const checkbox = dom.window.document.querySelector(`input[data-item-id="${itemId}"]`);
      checkbox.checked = true;
      checkbox.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    }
    assert.deepEqual(
      task(dom, taskId).current_attempt.state.selected_ids,
      taskFixture.required_ids,
    );
    dom.window.document.querySelector("#gallery-group-action").click();
    assert.equal(task(dom, taskId).status, "running");
    assert.deepEqual(
      task(dom, taskId).current_attempt.state.succeeded_ids,
      taskFixture.required_ids.filter((itemId) => itemId !== taskFixture.failed_id),
    );

    const failedCard = dom.window.document.querySelector(
      `.gallery-item[data-item-id="${taskFixture.failed_id}"]`,
    );
    failedCard.dispatchEvent(new dom.window.Event("mouseenter"));
    dom.window.document
      .querySelector(
        `.gallery-item[data-item-id="${taskFixture.failed_id}"] button[data-action="gallery-recover"]`,
      )
      .click();
    assert.equal(task(dom, taskId).status, "PASS");
    assert.deepEqual(Array.from(task(dom, taskId).last_receipt.evidence.recovery_ids), [
      taskFixture.failed_id,
    ]);
    assert.equal(
      task(dom, taskId).last_receipt.evidence.succeeded_ids.includes(taskFixture.failed_id),
      false,
    );
  } finally {
    dom.window.close();
  }
});

test("phase-A reducer rejects gallery membership and recovery false successes", () => {
  const taskId = "gallery-crew";
  let state = stateApi.createRunState({ run_id: "gallery-incomplete", suite_seed: "alpha" });
  state = stateApi.reduce(state, {
    type: "START",
    task_id: taskId,
    attempt_id: "gallery-attempt",
    at: 0,
  });
  const taskFixture = state.tasks[taskId].fixture;
  state = stateApi.reduce(state, {
    type: "GALLERY_HOVER",
    task_id: taskId,
    attempt_id: "gallery-attempt",
    item_id: taskFixture.required_ids[0],
    at: 1,
  });
  state = stateApi.reduce(state, {
    type: "GALLERY_GROUP_ACTION",
    task_id: taskId,
    attempt_id: "gallery-attempt",
    selected_ids: [taskFixture.required_ids[0]],
    at: 2,
  });
  assert.equal(state.tasks[taskId].last_receipt.reason.code, "incomplete-selection");
  const incompleteReceipt = state.tasks[taskId].last_receipt;
  state = stateApi.reduce(state, {
    type: "RETRY",
    task_id: taskId,
    attempt_id: "gallery-retry",
    at: 3,
  });
  assert.equal(state.tasks[taskId].status, "running");
  assert.equal(state.tasks[taskId].attempts.length, 1);
  assert.strictEqual(state.tasks[taskId].attempts[0], incompleteReceipt);
  assert.deepEqual(state.tasks[taskId].current_attempt.state.selected_ids, []);
  assert.equal(state.tasks[taskId].current_attempt.state.hovered_item_id, null);

  state = stateApi.createRunState({ run_id: "gallery-duplicate", suite_seed: "alpha" });
  state = stateApi.reduce(state, {
    type: "START",
    task_id: taskId,
    attempt_id: "gallery-duplicate",
    at: 0,
  });
  const duplicateFixture = state.tasks[taskId].fixture;
  state = stateApi.reduce(state, {
    type: "GALLERY_HOVER",
    task_id: taskId,
    attempt_id: "gallery-duplicate",
    item_id: duplicateFixture.required_ids[0],
    at: 1,
  });
  state = stateApi.reduce(state, {
    type: "GALLERY_GROUP_ACTION",
    task_id: taskId,
    attempt_id: "gallery-duplicate",
    selected_ids: [duplicateFixture.required_ids[0], duplicateFixture.required_ids[0]],
    at: 2,
  });
  assert.equal(state.tasks[taskId].last_receipt.reason.code, "duplicate-membership");

  state = stateApi.createRunState({ run_id: "gallery-recovery-false", suite_seed: "alpha" });
  state = stateApi.reduce(state, {
    type: "START",
    task_id: taskId,
    attempt_id: "gallery-recovery",
    at: 0,
  });
  const retryFixture = state.tasks[taskId].fixture;
  state = stateApi.reduce(state, {
    type: "GALLERY_HOVER",
    task_id: taskId,
    attempt_id: "gallery-recovery",
    item_id: retryFixture.required_ids[0],
    at: 1,
  });
  state = stateApi.reduce(state, {
    type: "GALLERY_GROUP_ACTION",
    task_id: taskId,
    attempt_id: "gallery-recovery",
    selected_ids: retryFixture.required_ids,
    at: 2,
  });
  const succeeded = state.tasks[taskId].current_attempt.state.succeeded_ids;
  state = stateApi.reduce(state, {
    type: "GALLERY_RECOVER",
    task_id: taskId,
    attempt_id: "gallery-recovery",
    item_ids: [...succeeded, retryFixture.failed_id],
    at: 3,
  });
  assert.equal(state.tasks[taskId].last_receipt.reason.code, "recovery-reapplies-successes");
});

test("queue keeper requires current-queue object identity and preserves order", () => {
  const dom = fixture();
  try {
    const taskId = "queue-keeper";
    click(dom, "select", taskId);
    click(dom, "start", taskId);
    const current = task(dom, taskId);
    const duplicate = current.fixture.catalog.find(
      (item) => item.title === current.fixture.target_title,
    );
    dom.window.document
      .querySelector(
        `button[data-action="queue-play"][data-source="catalog"][data-item-id="${duplicate.id}"]`,
      )
      .click();
    assert.equal(task(dom, taskId).status, "FAIL");
    assert.equal(task(dom, taskId).last_receipt.reason.code, "wrong-object-identity");
    assert.equal(task(dom, taskId).last_receipt.evidence.queue_replaced, true);

    click(dom, "retry", taskId);
    const target = task(dom, taskId).fixture.target_id;
    dom.window.document
      .querySelector(
        `button[data-action="queue-play"][data-source="queue"][data-item-id="${target}"]`,
      )
      .click();
    const receipt = task(dom, taskId).last_receipt;
    assert.equal(task(dom, taskId).status, "PASS");
    assert.equal(receipt.evidence.queue_preserved, true);
    assert.equal(receipt.evidence.clicked_object_id, target);
    assert.equal(receipt.evidence.playback_advances, 1);
    assert.deepEqual(
      receipt.evidence.queue_after_play.map((item) => item.id),
      receipt.evidence.expected_queue.map((item) => item.id),
    );
  } finally {
    dom.window.close();
  }
});

test("receipt ledger separates target search from exhaustive coverage and scores every row", () => {
  const dom = fixture();
  try {
    const taskId = "receipt-ledger";
    click(dom, "select", taskId);
    click(dom, "start", taskId);
    const taskFixture = task(dom, taskId).fixture;
    set(dom, "ledger-search", taskFixture.search_target_id);
    dom.window.document.querySelector("#ledger-search-button").click();
    assert.equal(task(dom, taskId).current_attempt.state.search_result.counts_as_inspection, false);
    assert.equal(task(dom, taskId).current_attempt.state.inspected_ids.length, 0);

    const filter = dom.window.document.querySelector("#ledger-filter");
    const sort = dom.window.document.querySelector("#ledger-sort");
    const direction = dom.window.document.querySelector("#ledger-direction");
    filter.value = taskFixture.filter_value;
    sort.value = taskFixture.sort_key;
    direction.value = taskFixture.sort_direction;
    dom.window.document.querySelector("#ledger-apply-view").click();
    const expectedIds = taskFixture.matching_ids;
    const partialId = expectedIds[0];
    dom.window.document
      .querySelector(`button[data-action="ledger-inspect"][data-item-id="${partialId}"]`)
      .click();
    set(dom, "ledger-ids", expectedIds.join(", "));
    set(dom, "ledger-total", String(taskFixture.expected_total));
    dom.window.document.querySelector("#ledger-submit").click();
    assert.equal(task(dom, taskId).status, "FAIL");
    assert.equal(task(dom, taskId).last_receipt.reason.code, "uninspected-row");
    assert.ok(task(dom, taskId).last_receipt.evidence.missing_ids.length > 0);

    click(dom, "retry", taskId);
    filter.value = taskFixture.filter_value;
    sort.value = taskFixture.sort_key;
    direction.value = taskFixture.sort_direction;
    dom.window.document.querySelector("#ledger-apply-view").click();
    for (const rowId of expectedIds) {
      dom.window.document
        .querySelector(`button[data-action="ledger-inspect"][data-item-id="${rowId}"]`)
        .click();
    }
    set(dom, "ledger-ids", expectedIds.join(", "));
    set(dom, "ledger-total", String(taskFixture.expected_total));
    dom.window.document.querySelector("#ledger-submit").click();
    assert.equal(task(dom, taskId).status, "PASS");
    assert.deepEqual(task(dom, taskId).last_receipt.evidence.inspected_ids, expectedIds);
    assert.equal(task(dom, taskId).last_receipt.evidence.search_counts_as_inspection, false);
  } finally {
    dom.window.close();
  }
});

test("recovery room handles an in-page rename conflict without duplicate submission", () => {
  const dom = fixture();
  try {
    const taskId = "recovery-room";
    click(dom, "select", taskId);
    click(dom, "start", taskId);
    dom.window.document.querySelector("#recovery-submit").click();
    assert.equal(task(dom, taskId).status, "running");
    assert.equal(dom.window.document.querySelector("#recovery-conflict-dialog").hidden, false);
    assert.equal(
      dom.window.document.querySelector("#recovery-conflict-dialog").getAttribute("aria-label"),
      "In-page rename conflict",
    );

    const attemptId = task(dom, taskId).current_attempt.attempt_id;
    dom.window.CUNativePlayground.dispatch("RECOVERY_SUBMIT", taskId, {
      attempt_id: attemptId,
      at: 2,
    });
    assert.equal(task(dom, taskId).status, "FAIL");
    assert.equal(task(dom, taskId).last_receipt.reason.code, "duplicate-submission");

    click(dom, "retry", taskId);
    const taskFixture = task(dom, taskId).fixture;
    dom.window.document.querySelector("#recovery-submit").click();
    set(dom, "recovery-rename", taskFixture.rename_to);
    dom.window.document.querySelector("#recovery-confirm").click();
    assert.equal(task(dom, taskId).current_attempt.state.conflict_resolved, true);
    dom.window.document.querySelector("#recovery-resume").click();
    assert.equal(task(dom, taskId).status, "PASS");
    assert.equal(task(dom, taskId).last_receipt.evidence.job_submissions, 1);
    assert.equal(task(dom, taskId).last_receipt.evidence.resume_count, 1);
    assert.equal(task(dom, taskId).last_receipt.evidence.data_preserved, true);
    assert.notEqual(
      task(dom, taskId).last_receipt.evidence.conflict.existing_data.payload,
      taskFixture.payload,
    );
  } finally {
    dom.window.close();
  }
});

test("changed desk rejects stale and adjacent controls, then accepts a fresh target", async () => {
  const dom = fixture();
  try {
    const taskId = "changed-desk";
    click(dom, "select", taskId);
    click(dom, "start", taskId);
    const firstAttempt = task(dom, taskId).current_attempt.attempt_id;
    dom.window.document.querySelector("#changed-schedule").click();
    await waitFor(
      () => task(dom, taskId).current_attempt?.state.layout_changed,
      "desk did not change",
    );
    dom.window.CUNativePlayground.dispatch("CHANGED_CONFIRM", taskId, {
      attempt_id: firstAttempt,
      target_id: task(dom, taskId).fixture.old_target_id,
      layout_version: 2,
      at: 3,
    });
    assert.equal(task(dom, taskId).status, "FAIL");
    assert.equal(task(dom, taskId).last_receipt.reason.code, "stale-target");

    click(dom, "retry", taskId);
    dom.window.document.querySelector("#changed-schedule").click();
    await waitFor(
      () => task(dom, taskId).current_attempt?.state.layout_changed,
      "desk retry did not change",
    );
    dom.window.document.querySelector("#changed-decoy").click();
    assert.equal(task(dom, taskId).status, "FAIL");
    assert.equal(task(dom, taskId).last_receipt.reason.code, "decoy-target");
    assert.match(task(dom, taskId).last_receipt.evidence.effect, /non-destructive/);
    assert.equal(task(dom, taskId).last_receipt.evidence.committed_actions, 0);

    click(dom, "retry", taskId);
    dom.window.document.querySelector("#changed-schedule").click();
    await waitFor(
      () => task(dom, taskId).current_attempt?.state.layout_changed,
      "desk valid attempt did not change",
    );
    dom.window.document.querySelector("#changed-refresh").click();
    dom.window.document.querySelector("#changed-confirm").click();
    assert.equal(task(dom, taskId).status, "PASS");
    assert.equal(task(dom, taskId).last_receipt.evidence.fresh_observations, 1);
    assert.equal(task(dom, taskId).last_receipt.evidence.committed_actions, 1);
  } finally {
    dom.window.close();
  }
});

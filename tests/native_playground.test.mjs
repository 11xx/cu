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
    assert.equal(dom.window.document.querySelector("#suite-progress").textContent.includes("1/2 PASS"), true);
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
  state = stateApi.reduce(state, { type: "ENTRY_INPUT", task_id: "native-basics", value: "ignored", at: 5200 });
  assert.strictEqual(state.tasks["native-basics"].last_receipt, receipt);

  state = stateApi.createRunState({ run_id: "run-abort", at: 0 });
  state = stateApi.reduce(state, {
    type: "START",
    task_id: "native-basics",
    attempt_id: "attempt-user",
    at: 0,
  });
  state = stateApi.reduce(state, { type: "ABORT", task_id: "native-basics", cause: "user", at: 10 });
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

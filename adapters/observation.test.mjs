import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { observeTogether, runCu, scanCoverage, seekTarget } from "./observation.mjs";

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=", "base64");
async function fixture(t, mode = "ok") {
  const dir = await mkdtemp(join(tmpdir(), "cu-adapter-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const image = join(dir, "f1.png");
  await writeFile(image, png);
  const frame = { id: "f1", path: image, image_size: { w: 1, h: 1 }, encoded_bytes: png.length,
    digest_native: createHash("sha256").update(png).digest("hex"),
    logical_rect: { x: 10, y: 20, w: 2, h: 2 }, window: { address: "0x1", content_id: "fixture" } };
  if (mode === "missing") frame.path += ".missing";
  if (mode === "mismatch") frame.image_size.w = 2;
  if (mode === "digest") frame.digest_native = "0".repeat(64);
  if (mode === "budget") frame.encoded_bytes = 4 * 1024 * 1024 + 1;
  const executable = join(dir, "cu");
  await writeFile(executable, `#!/usr/bin/env node
const args = process.argv.slice(2);
const mode = ${JSON.stringify(mode)};
if (mode === 'hang') setInterval(() => {}, 1000);
else if (mode === 'huge') console.log('x'.repeat(70000));
else if (args[0] === 'batch') {
  process.stdin.resume();
  process.stdin.on('end', () => {
    if (mode === 'empty') return;
    if (mode === 'malformed-refuse') {
      console.log('not JSON');
      process.exitCode = 32;
      return;
    }
    const code = mode === 'refuse' ? 40 : 0;
    const step = mode === 'appeared'
      ? {step: 1, result: {appeared: {status: 'observed', windows: [{address: '0x2', monitor: 'DP-1', title: 'Cannot open file'}]}}}
      : {step: 1};
    console.log(JSON.stringify({code, completed: code ? [] : [step], failed: code ? {step: 1, code} : null}));
    process.exitCode = code;
  });
} else {
  if (mode === 'appeared') {
    const path = require('node:path');
    require('node:fs').writeFileSync(path.join(path.dirname(process.argv[1]), 'shot-args.json'), JSON.stringify(args));
  }
  if (mode === 'refuse') throw new Error('capture must not run after refusal');
  if (mode === 'capture-fail') { console.error('capture unavailable'); process.exitCode = 1; }
  else console.log(${JSON.stringify(JSON.stringify(frame))});
}
`, { mode: 0o700 });
  return { executable, frame };
}

test("one result carries unchanged descriptor and exact PNG", async t => {
  const { executable, frame } = await fixture(t);
  const result = await observeTogether({ actions: [["key", "TAB"]] }, { executable });
  assert.equal(result.details.ok, true);
  assert.equal(result.details.actions.status, "completed");
  assert.deepEqual(JSON.parse(result.content[0].text).observation, frame);
  assert.deepEqual(Buffer.from(result.content[1].data, "base64"), png);
});

test("a window that appeared during the actions widens the capture to its output", async t => {
  const { executable } = await fixture(t, "appeared");
  const result = await observeTogether(
    { actions: [["key", "--chord", "return"]], shot: ["--window", "active"] },
    { executable },
  );
  assert.equal(result.details.ok, true);
  assert.deepEqual(result.details.appeared, [
    { address: "0x2", monitor: "DP-1", title: "Cannot open file", step: 1 },
  ]);
  assert.deepEqual(result.details.capture_override, {
    reason: "window-appeared",
    requested: ["--window", "active"],
    used: ["--output", "DP-1"],
  });
  const { readFile } = await import("node:fs/promises");
  const { dirname } = await import("node:path");
  const shotArgs = JSON.parse(await readFile(join(dirname(executable), "shot-args.json"), "utf8"));
  assert.deepEqual(shotArgs.slice(0, 3), ["shot", "--output", "DP-1"]);
});

test("read-only call makes no action-success claim", async t => {
  const options = await fixture(t);
  const result = await observeTogether({}, options);
  assert.equal(result.details.actions.status, "not-requested");
  assert.equal(result.content.length, 2);
});

for (const mode of ["refuse", "capture-fail", "missing", "mismatch", "digest", "budget", "huge"]) {
  test(`${mode}: no frame or image is published`, async t => {
    const options = await fixture(t, mode);
    const result = await observeTogether({ actions: [["key", "TAB"]] }, options);
    assert.equal(result.details.ok, false);
    assert.equal(result.details.observation, null);
    assert.equal(result.content.length, 1);
    if (mode === "refuse") assert.equal(result.details.actions.code, 40);
    if (mode === "capture-fail") assert.equal(result.details.actions.status, "completed");
  });
}

test("failed call never reuses previous successful observation", async t => {
  const good = await fixture(t);
  const bad = await fixture(t, "missing");
  assert.equal((await observeTogether({}, good)).details.ok, true);
  assert.equal((await observeTogether({}, bad)).details.observation, null);
});

test("invalid capture options refuse before actions", async () => {
  const result = await observeTogether({ actions: [["key", "TAB"]], shot: ["--help", "yes"] }, { executable: "/nonexistent" });
  assert.equal(result.details.actions.status, "not-started");
  assert.equal(result.details.error.phase, "request");
});

test("missing batch receipt fails before capture", async t => {
  const options = await fixture(t, "empty");
  const result = await observeTogether({ actions: [["move", "--frame", "f999", "--at", "1,1"]] }, options);
  assert.equal(result.details.ok, false);
  assert.equal(result.details.actions.status, "unknown");
  assert.equal(result.details.actions.code, 0);
  assert.equal(result.details.actions.may_have_acted, true);
  assert.equal(result.details.error.phase, "actions");
  assert.match(result.details.error.detail, /no JSON result/);
  assert.equal(result.content.length, 1);
});

test("a malformed refusal keeps the child exit code", async t => {
  const options = await fixture(t, "malformed-refuse");
  const result = await observeTogether({ actions: [["move", "--frame", "f999", "--at", "1,1"]] }, options);
  assert.equal(result.details.ok, false);
  assert.equal(result.details.actions.status, "unknown");
  assert.equal(result.details.actions.code, 32);
  assert.equal(result.details.actions.may_have_acted, true);
  assert.equal(result.details.error.phase, "actions");
  assert.equal(result.details.error.code, 32);
  assert.match(result.details.error.detail, /invalid JSON/);
  assert.equal(result.content.length, 1);
});

test("deadline terminates child and does not imply success", async t => {
  const options = await fixture(t, "hang");
  await assert.rejects(runCu([], "", { ...options, timeout: 50 }), /deadline/);
});

test("cancellation prevents invocation", async () => {
  const signal = AbortSignal.abort();
  const result = await observeTogether({}, { signal, executable: "/nonexistent" });
  assert.equal(result.details.ok, false);
  assert.equal(result.details.observation, null);
});

function policyFrame(id, source = "seed", address = "0x1") {
  return {
    id,
    source,
    image_size: { w: 100, h: 100 },
    logical_rect: { x: 0, y: 0, w: 100, h: 100 },
    window: { address },
  };
}

function policyReport(
  id,
  { source = "seed", address = "0x1", actions = false, status = "completed", code = 0 } = {},
) {
  const frame = policyFrame(id, source, address);
  return {
    details: {
      schema: "cu-observation/2",
      ok: status === "completed" && actions ? true : !actions,
      actions: actions ? { status, code, result: { step: 1 } } : { status: "not-requested" },
      observation: status === "completed" || !actions ? frame : null,
    },
  };
}

function policyRequest(overrides = {}) {
  return {
    target: "Bottom",
    windowAddress: "0x1",
    containerId: "inner",
    at: { x: 20, y: 30 },
    shot: ["--window", "active"],
    direction: 1,
    initialStep: 1,
    maxStep: 8,
    maxInput: 16,
    maxTravel: 32,
    deadlineMs: 1000,
    maxNoProgress: 2,
    maxBacktracks: 2,
    ...overrides,
  };
}

function fakePolicyObserver({ sources = [], refusalAt = null, onCall } = {}) {
  let calls = 0;
  return {
    get calls() {
      return calls;
    },
    observe: async ({ actions }) => {
      calls += 1;
      onCall?.({ actions, call: calls });
      if (refusalAt === calls)
        return policyReport(`f${calls}`, { actions: true, status: "refused", code: 32 });
      return policyReport(`f${calls}`, {
        actions: actions.length > 0,
        source: sources[calls - 1] ?? "seed",
      });
    },
  };
}

function seekEvidence(states) {
  let index = 0;
  return async () => states[index++];
}

function seekState(position, state = "not-observed", extra = {}) {
  return {
    source: "seed",
    container: { id: "inner", position },
    target: {
      state,
      verified: false,
      postcondition: false,
      authoritative: false,
      ...extra,
    },
  };
}

test("SEEK grows, shrinks near a candidate, backtracks, and requires verification", async () => {
  const actions = [];
  const transport = fakePolicyObserver({ onCall: (call) => actions.push(call.actions) });
  const result = await seekTarget(policyRequest(), {
    observe: transport.observe,
    assess: seekEvidence([
      seekState(0),
      seekState(1),
      seekState(3, "candidate", { near: true }),
      { ...seekState(4, "visible"), overshot: true },
      seekState(3, "reached", { verified: true, postcondition: true }),
    ]),
  });
  assert.equal(result.status, "reached");
  assert.deepEqual(
    actions.slice(1).map((batch) => batch[0].at(-1)),
    ["1", "2", "1", "-1"],
  );
  assert.equal(result.usage.backtracks, 1);
  assert.equal(result.attempts.length, 5);
  assert.equal(result.attempts.at(-1).frame_id, "f5");
  assert.equal(result.attempts.at(-1).receipt.status, "completed");
  assert.equal(result.final.evidence.target.postcondition, true);
});

test("SEEK distinguishes absent and not-yet-observed targets", async () => {
  const absent = await seekTarget(policyRequest(), {
    observe: fakePolicyObserver().observe,
    assess: seekEvidence([seekState(0, "absent", { authoritative: true })]),
  });
  assert.equal(absent.status, "absent");
  assert.equal(absent.usage.steps, 0);

  const unseen = await seekTarget(policyRequest({ maxInput: 1 }), {
    observe: fakePolicyObserver().observe,
    assess: seekEvidence([seekState(0), seekState(1)]),
  });
  assert.equal(unseen.status, "not-yet-observed");
  assert.match(unseen.reason, /input budget/);

  const candidate = await seekTarget(policyRequest({ maxInput: 1 }), {
    observe: fakePolicyObserver().observe,
    assess: seekEvidence([seekState(0), seekState(1, "candidate")]),
  });
  assert.equal(candidate.status, "not-reached-within-budget");
});

test("SEEK stops on the wrong container, no progress, mutation, refusal, and cancellation", async (t) => {
  await t.test("wrong container", async () => {
    const result = await seekTarget(policyRequest(), {
      observe: fakePolicyObserver().observe,
      assess: seekEvidence([{ ...seekState(0), container: { id: "outer", position: 0 } }]),
    });
    assert.equal(result.status, "wrong-container");
    assert.equal(result.usage.steps, 0);
  });

  await t.test("no progress", async () => {
    const result = await seekTarget(policyRequest({ maxNoProgress: 1 }), {
      observe: fakePolicyObserver().observe,
      assess: seekEvidence([seekState(0), seekState(0)]),
    });
    assert.equal(result.status, "not-yet-observed");
    assert.match(result.reason, /no progress/);
  });

  await t.test("mutation", async () => {
    const transport = fakePolicyObserver({ sources: ["seed", "changed"] });
    const result = await seekTarget(policyRequest(), {
      observe: transport.observe,
      assess: seekEvidence([seekState(0), { ...seekState(1), source: "changed" }]),
    });
    assert.equal(result.status, "mutated");
  });

  await t.test("refusal preserves the receipt and does not replay", async () => {
    const transport = fakePolicyObserver({ refusalAt: 2 });
    const result = await seekTarget(policyRequest(), {
      observe: transport.observe,
      assess: seekEvidence([seekState(0), seekState(1)]),
    });
    assert.equal(result.status, "refused");
    assert.equal(transport.calls, 2);
    assert.equal(result.attempts.at(-1).receipt.code, 32);
  });

  await t.test("cancellation after a dispatched step preserves that step", async () => {
    const signal = { aborted: false };
    const transport = fakePolicyObserver({
      onCall: ({ call }) => {
        if (call === 2) signal.aborted = true;
      },
    });
    const result = await seekTarget(policyRequest(), {
      signal,
      observe: transport.observe,
      assess: seekEvidence([seekState(0), seekState(1)]),
    });
    assert.equal(result.status, "cancelled");
    assert.equal(result.attempts.at(-1).receipt.status, "completed");
    assert.equal(transport.calls, 2);
  });
});

function scanState(position, start, end, atEnd = false, source = "seed") {
  return {
    source,
    container: { id: "inner", position },
    coverage: { start, end },
    end: atEnd,
  };
}

function scanRequest(overrides = {}) {
  return {
    windowAddress: "0x1",
    containerId: "inner",
    at: [20, 30],
    shot: ["--window", "active"],
    direction: 1,
    step: 1,
    range: { start: 0, end: 12 },
    overlap: 1,
    maxInput: 8,
    maxTravel: 8,
    deadlineMs: 1000,
    maxNoProgress: 2,
    ...overrides,
  };
}

test("SCAN keeps source/frame references and completes only with overlap", async () => {
  const transport = fakePolicyObserver();
  const result = await scanCoverage(scanRequest(), {
    observe: transport.observe,
    assess: seekEvidence([scanState(0, 0, 5), scanState(1, 4, 9), scanState(2, 8, 12, true)]),
  });
  assert.equal(result.status, "complete");
  assert.equal(result.coverage.complete, true);
  assert.deepEqual(
    result.coverage.segments.map((segment) => segment.frame_id),
    ["f1", "f2", "f3"],
  );
  assert.deepEqual(
    result.coverage.segments.map((segment) => segment.source),
    ["seed", "seed", "seed"],
  );
  assert.deepEqual(result.coverage.gaps, []);
  assert.deepEqual(
    result.attempts.slice(1).map((attempt) => attempt.action.at(-1)),
    ["1", "1"],
  );
});

test("SCAN refuses to call a gap complete", async () => {
  const result = await scanCoverage(scanRequest(), {
    observe: fakePolicyObserver().observe,
    assess: seekEvidence([scanState(0, 0, 5), scanState(1, 7, 12, true)]),
  });
  assert.equal(result.status, "incomplete");
  assert.equal(result.coverage.complete, false);
  assert.deepEqual(result.coverage.gaps, [{ start: 5, end: 7 }]);
  assert.equal(result.coverage.overlap_violations.length, 1);
  assert.equal(result.coverage.segments.length, 2);
});

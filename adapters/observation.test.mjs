import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { observeTogether, runCu } from "./observation.mjs";

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
    console.log(JSON.stringify({code, completed: code ? [] : [{step: 1}], failed: code ? {step: 1, code} : null}));
    process.exitCode = code;
  });
} else {
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

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { open } from "node:fs/promises";

const MAX_TEXT = 64 * 1024;
const MAX_IMAGE = 4 * 1024 * 1024;
const SHOT_FLAGS = new Set(["--window", "--output", "--region", "--target-window", "--landmark", "--context", "--for", "--after", "--watch"]);

// No shell, retries, caches, or input-device access: cu owns execution.
export function runCu(argv, input, { signal, cwd, executable = "cu", timeout = 120000 } = {}) {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const child = spawn(executable, argv, { cwd, detached: true, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "", stderr = "", bytes = 0, failure;
    const stop = (error) => {
      failure ??= error;
      if (child.pid) {
        try { process.kill(-child.pid, "SIGKILL"); } catch (e) { if (e.code !== "ESRCH") failure = e; }
      }
    };
    const abort = () => stop(new Error("cancelled; action outcome may be partial"));
    const timer = setTimeout(() => stop(new Error("adapter deadline exceeded; action outcome may be partial")), timeout);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    const collect = (target) => (chunk) => {
      bytes += Buffer.byteLength(chunk);
      if (bytes > MAX_TEXT) return stop(new Error("cu output exceeds 64 KiB; action outcome may be partial"));
      if (target === "stdout") stdout += chunk; else stderr += chunk;
    };
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", collect("stdout"));
    child.stderr.on("data", collect("stderr"));
    child.stdin.on("error", (error) => { if (error.code !== "EPIPE") stop(error); });
    child.on("error", (error) => { failure = error; });
    child.on("close", (code, killedBy) => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      if (failure) reject(failure);
      else if (code === null) reject(new Error(`cu terminated by ${killedBy}; outcome unknown`));
      else resolve({ code, stdout, stderr });
    });
    child.stdin.end(input ?? "");
  });
}

function validate(request) {
  const actions = request.actions ?? [];
  const shot = request.shot ?? [];
  if (!Array.isArray(actions) || actions.length > 32 ||
      !actions.every(a => Array.isArray(a) && a.length && a.every(s => typeof s === "string"))) {
    throw new Error("actions must be at most 32 nonempty argv arrays");
  }
  if (Buffer.byteLength(JSON.stringify(actions)) > MAX_TEXT) throw new Error("actions exceed 64 KiB");
  if (!Array.isArray(shot) || shot.length > 16 || shot.length % 2 ||
      !shot.every(s => typeof s === "string" && s.length <= 4096)) {
    throw new Error("shot must contain bounded flag/value pairs");
  }
  for (let i = 0; i < shot.length; i += 2) {
    if (!SHOT_FLAGS.has(shot[i]) || shot[i + 1].startsWith("--")) throw new Error("unsupported shot option");
  }
  for (const key of ["session", "why"]) {
    if (request[key] !== undefined && (typeof request[key] !== "string" || request[key].length > 4096)) {
      throw new Error(`${key} must be a bounded string`);
    }
  }
  return { actions, shot };
}

async function imageFor(frame) {
  if (!/^f[1-9][0-9]*$/.test(frame.id) || typeof frame.path !== "string" ||
      !Number.isInteger(frame.encoded_bytes) || frame.encoded_bytes < 33 || frame.encoded_bytes > MAX_IMAGE) {
    throw new Error("invalid frame descriptor or image exceeds 4 MiB");
  }
  const file = await open(frame.path, "r");
  let data;
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size !== frame.encoded_bytes) throw new Error("frame image size mismatch");
    data = Buffer.alloc(frame.encoded_bytes + 1);
    let used = 0;
    while (used < data.length) {
      const { bytesRead } = await file.read(data, used, data.length - used);
      if (!bytesRead) break;
      used += bytesRead;
    }
    if (used !== frame.encoded_bytes) throw new Error("frame image changed while reading");
    data = data.subarray(0, used);
  } finally { await file.close(); }
  if (data.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a" ||
      data.readUInt32BE(16) !== frame.image_size?.w || data.readUInt32BE(20) !== frame.image_size?.h ||
      createHash("sha256").update(data).digest("hex") !== frame.digest_native) {
    throw new Error("frame PNG does not match its descriptor");
  }
  return { type: "image", mimeType: "image/png", data: data.toString("base64") };
}

// The result's text is the portable contract; image bytes are a sibling block.
export async function observeTogether(request, options = {}) {
  const report = { schema: "cu-observation/2", ok: false, actions: { status: "not-started" }, observation: null };
  let phase = "request";
  try {
    const { actions, shot } = validate(request);
    const common = ["--json"];
    if (request.session) common.push("--session", request.session);
    if (request.why) common.push("--why", request.why);
    if (actions.length) {
      phase = "actions";
      report.actions = { status: "unknown", may_have_acted: true };
      const result = await runCu(["batch", "-", ...common], JSON.stringify(actions), options);
      const actionResult = result.stdout.trim() ? JSON.parse(result.stdout) : null;
      report.actions = { status: result.code === 0 ? "completed" : "refused", code: result.code,
        result: actionResult, detail: result.stderr };
      if (actionResult?.invocation_id) {
        report.correlation = { actions_invocation_id: actionResult.invocation_id };
      }
      if (result.code !== 0) return textResult(report);
    } else report.actions = { status: "not-requested" };
    phase = "capture";
    const result = await runCu(["shot", ...shot, ...common], "", options);
    if (result.code !== 0) {
      report.error = { phase, code: result.code, detail: result.stderr };
      return textResult(report);
    }
    const frame = JSON.parse(result.stdout);
    phase = "image";
    const image = await imageFor(frame);
    options.signal?.throwIfAborted();
    report.observation = frame;
    report.correlation = {
      ...(report.correlation ?? {}),
      ...(frame.invocation_id ? { observation_invocation_id: frame.invocation_id } : {}),
    };
    report.ok = true;
    return { ...textResult(report), content: [{ type: "text", text: JSON.stringify(report) }, image] };
  } catch (error) {
    report.error = { phase, detail: error.message };
    return textResult(report);
  }
}

function textResult(report) {
  return { content: [{ type: "text", text: JSON.stringify(report) }], details: report };
}

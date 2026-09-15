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

// A window that opened during the actions is most likely the application's
// answer, and a capture bound to the old target can exclude it, so the capture
// widens to the output that holds the new window.
function appearedWindows(batch) {
  const steps = Array.isArray(batch?.completed) ? batch.completed : [];
  return steps.flatMap((step) => {
    const windows = step?.result?.appeared?.windows;
    return Array.isArray(windows) ? windows.map((window) => ({ ...window, step: step.step })) : [];
  });
}

function captureForAppeared(appeared, requested) {
  const monitor = appeared.find((window) => typeof window.monitor === "string" && window.monitor)?.monitor;
  if (!monitor) return null;
  return { reason: "window-appeared", requested: [...requested], used: ["--output", monitor] };
}

function parseJsonObject(output, name) {
  const text = output.trim();
  if (!text) throw new Error(`${name} returned no JSON result`);
  let value;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new Error(`${name} returned invalid JSON: ${error.message}`);
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} result must be a JSON object`);
  }
  return value;
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
      let actionResult;
      try {
        actionResult = parseJsonObject(result.stdout, "cu batch");
      } catch (error) {
        report.actions = {
          status: "unknown",
          code: result.code,
          result: null,
          detail: result.stderr || error.message,
          may_have_acted: true,
        };
        report.error = {
          phase,
          ...(result.code !== 0 ? { code: result.code } : {}),
          detail: error.message,
        };
        return textResult(report);
      }
      report.actions = { status: result.code === 0 ? "completed" : "refused", code: result.code,
        result: actionResult, detail: result.stderr };
      if (actionResult?.invocation_id) {
        report.correlation = { actions_invocation_id: actionResult.invocation_id };
      }
      if (result.code !== 0) return textResult(report);
      const appeared = appearedWindows(actionResult);
      if (appeared.length) report.appeared = appeared;
    } else report.actions = { status: "not-requested" };
    phase = "capture";
    const override = report.appeared ? captureForAppeared(report.appeared, shot) : null;
    if (override) report.capture_override = override;
    const result = await runCu(["shot", ...(override?.used ?? shot), ...common], "", options);
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

const POLICY_SCHEMA = "cu-scroll-policy/1";
const MAX_POLICY_STEPS = 128;
const MAX_POLICY_STEP = 1024;
const MAX_POLICY_INPUT = 4096;
const MAX_POLICY_DEADLINE = 120000;
const MAX_POLICY_BACKTRACKS = 32;
const POLICY_AXES = new Set(["vertical", "horizontal"]);
const SEEK_STATES = new Set(["absent", "not-observed", "candidate", "visible", "reached"]);

function policyError(message) {
  throw new Error(`scroll policy: ${message}`);
}

function policyString(value, name) {
  if (typeof value !== "string" || !value || value.length > 4096) {
    policyError(`${name} must be a non-empty string of at most 4096 characters`);
  }
  return value;
}

function policyNumber(value, name, { integer = false, min = -Infinity, max = Infinity } = {}) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < min ||
    value > max ||
    (integer && !Number.isInteger(value))
  ) {
    const kind = integer ? "finite integer" : "finite number";
    policyError(`${name} must be a ${kind} in [${min}, ${max}]`);
  }
  return value;
}

function policyPoint(value) {
  const point = Array.isArray(value) ? { x: value[0], y: value[1] } : value;
  if (!point || typeof point !== "object") policyError("at must contain image x and y coordinates");
  return {
    x: policyNumber(point.x, "at.x", { min: 0 }),
    y: policyNumber(point.y, "at.y", { min: 0 }),
  };
}

function stablePolicyKey(value) {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (Array.isArray(value)) return `[${value.map(stablePolicyKey).join(",")}]`;
  if (typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stablePolicyKey(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function policySourceReference(value) {
  if (value === undefined || value === null) return value;
  if (stablePolicyKey(value).length > 4096) {
    policyError("source must be a bounded reference of at most 4096 characters");
  }
  return value;
}

function addressKey(value) {
  return typeof value === "string" ? value.replace(/^0x/i, "").toLowerCase() : null;
}

function policyNow(options) {
  const now = options.now ?? (() => globalThis.performance?.now?.() ?? Date.now());
  if (typeof now !== "function") policyError("now must be a function when supplied");
  return now;
}

function validateCommonPolicy(request, mode) {
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    policyError(`${mode} request must be an object`);
  }
  const config = {
    mode,
    windowAddress: policyString(request.windowAddress, "windowAddress"),
    containerId: policyString(request.containerId, "containerId"),
    at: policyPoint(request.at),
    shot: request.shot ?? [],
    axis: request.axis ?? "vertical",
    direction: request.direction ?? -1,
    maxInput: policyNumber(request.maxInput, "maxInput", {
      integer: true,
      min: 1,
      max: MAX_POLICY_INPUT,
    }),
    maxTravel: policyNumber(request.maxTravel, "maxTravel", { min: Number.MIN_VALUE }),
    deadlineMs: policyNumber(request.deadlineMs, "deadlineMs", {
      min: Number.MIN_VALUE,
      max: MAX_POLICY_DEADLINE,
    }),
    maxNoProgress: policyNumber(request.maxNoProgress, "maxNoProgress", {
      integer: true,
      min: 0,
      max: MAX_POLICY_STEPS,
    }),
    session: request.session,
    why: request.why,
  };
  if (!Array.isArray(config.shot)) policyError("shot must be an array of flag/value strings");
  if (
    config.shot.length > 16 ||
    config.shot.length % 2 ||
    !config.shot.every((value) => typeof value === "string" && value.length <= 4096)
  ) {
    policyError("shot must contain at most eight bounded flag/value pairs");
  }
  for (let index = 0; index < config.shot.length; index += 2) {
    if (!SHOT_FLAGS.has(config.shot[index]) || config.shot[index + 1].startsWith("--")) {
      policyError("shot contains an unsupported option");
    }
  }
  if (!POLICY_AXES.has(config.axis)) policyError("axis must be vertical or horizontal");
  if (config.direction !== 1 && config.direction !== -1) policyError("direction must be 1 or -1");
  if (config.session !== undefined) policyString(config.session, "session");
  if (config.why !== undefined) policyString(config.why, "why");
  return config;
}

function validateSeekPolicy(request) {
  const config = validateCommonPolicy(request, "SEEK");
  config.target = policyString(request.target, "target");
  config.initialStep = policyNumber(request.initialStep, "initialStep", {
    integer: true,
    min: 1,
    max: MAX_POLICY_STEP,
  });
  config.maxStep = policyNumber(request.maxStep, "maxStep", {
    integer: true,
    min: config.initialStep,
    max: MAX_POLICY_STEP,
  });
  config.maxBacktracks = policyNumber(request.maxBacktracks ?? 0, "maxBacktracks", {
    integer: true,
    min: 0,
    max: MAX_POLICY_BACKTRACKS,
  });
  return config;
}

function validateScanPolicy(request) {
  const config = validateCommonPolicy(request, "SCAN");
  config.step = policyNumber(request.step, "step", { integer: true, min: 1, max: MAX_POLICY_STEP });
  if (!request.range || typeof request.range !== "object")
    policyError("range must contain start and end");
  config.range = {
    start: policyNumber(request.range.start, "range.start"),
    end: policyNumber(request.range.end, "range.end"),
  };
  if (config.range.end <= config.range.start)
    policyError("range.end must be greater than range.start");
  config.overlap = policyNumber(request.overlap, "overlap", {
    min: 0,
    max: config.range.end - config.range.start,
  });
  return config;
}

function policyOptions(options) {
  const settings = options ?? {};
  if (typeof settings.observe !== "function" && settings.observe !== undefined) {
    policyError("observe must be a function when supplied");
  }
  if (typeof settings.assess !== "function") {
    policyError("assess must be a caller-owned function");
  }
  return {
    ...settings,
    observe: settings.observe ?? observeTogether,
    now: policyNow(settings),
  };
}

function isCancelled(signal) {
  return signal?.aborted === true;
}

function policyFrame(report) {
  const frame = report?.details?.observation;
  return frame && typeof frame === "object" && /^f[1-9][0-9]*$/.test(frame.id) ? frame : null;
}

function policySource(frame, evidence) {
  if (evidence && Object.prototype.hasOwnProperty.call(evidence, "source")) return evidence.source;
  return frame?.source;
}

function policyBinding(frame) {
  return frame?.window?.address ?? frame?.target_window?.address ?? null;
}

function classifyPolicyReport(report, acted) {
  const details = report?.details;
  if (!details || typeof details !== "object") return acted ? "uncertain" : "observation-failed";
  if (acted) {
    const actions = details.actions;
    if (!actions || actions.status === "unknown" || actions.may_have_acted === true)
      return "uncertain";
    if (actions.status === "refused" || (actions.code !== undefined && actions.code !== 0))
      return "refused";
    if (actions.status !== "completed") return "uncertain";
    if (details.ok !== true || !details.observation) return "uncertain";
  } else if (details.ok !== true || !details.observation) {
    return "observation-failed";
  }
  return null;
}

function policyAttempt(
  report,
  { phase, attempt, action, step, direction, source, evidence, error } = {},
) {
  const details = report?.details ?? null;
  const frame = policyFrame(report);
  return {
    phase,
    attempt,
    action: action ? [...action] : null,
    step: step ?? null,
    direction: direction ?? null,
    receipt: details?.actions ?? null,
    frame,
    frame_id: frame?.id ?? null,
    source: source ?? frame?.source ?? null,
    evidence: evidence ?? null,
    adapter: details,
    ...(error ? { error } : {}),
  };
}

function scrollAction(frameId, at, axis, delta) {
  const action = ["scroll", "--frame", frameId, "--at", `${at.x},${at.y}`];
  action.push(axis === "vertical" ? "--dy" : "--dx", String(delta));
  return action;
}

async function policyObserve(request, actions, settings, deadlineAt) {
  if (isCancelled(settings.signal)) return { cancelled: true };
  const remaining = deadlineAt - settings.now();
  if (remaining <= 0) return { deadline: true };
  const call = {
    actions,
    shot: [...request.shot],
    ...(request.session !== undefined ? { session: request.session } : {}),
    ...(request.why !== undefined ? { why: request.why } : {}),
  };
  try {
    const report = await settings.observe(call, {
      signal: settings.signal,
      cwd: settings.cwd,
      executable: settings.executable,
      timeout: Math.min(remaining, MAX_POLICY_DEADLINE),
    });
    return {
      report,
      cancelled: isCancelled(settings.signal),
      deadline: settings.now() > deadlineAt,
    };
  } catch (error) {
    return {
      error: error instanceof Error ? error.message : String(error),
      cancelled: isCancelled(settings.signal),
      deadline: settings.now() > deadlineAt,
    };
  }
}

function validateBoundFrame(request, report) {
  const frame = policyFrame(report);
  if (!frame) return { status: "observation-failed", reason: "no valid frame was returned" };
  const bound = addressKey(policyBinding(frame));
  if (bound !== addressKey(request.windowAddress)) {
    return {
      status: "wrong-window",
      reason: `expected window ${request.windowAddress}, got ${policyBinding(frame) ?? "none"}`,
      frame,
    };
  }
  return { frame, source: policySource(frame, null) };
}

function policyContainer(evidence) {
  if (
    !evidence ||
    typeof evidence !== "object" ||
    !evidence.container ||
    typeof evidence.container !== "object"
  ) {
    policyError("assess must return a container object");
  }
  return {
    id: policyString(evidence.container.id, "container.id"),
    position: policyNumber(evidence.container.position, "container.position"),
  };
}

function normaliseSeekEvidence(evidence) {
  if (
    !evidence ||
    typeof evidence !== "object" ||
    !evidence.target ||
    typeof evidence.target !== "object"
  ) {
    policyError("SEEK assess must return target evidence");
  }
  const target = evidence.target;
  if (!SEEK_STATES.has(target.state)) {
    policyError(`SEEK target.state must be one of ${[...SEEK_STATES].join(", ")}`);
  }
  if (target.state === "absent" && target.authoritative !== true) {
    policyError("SEEK target absence must be authoritative");
  }
  return {
    source: policySourceReference(evidence.source),
    container: policyContainer(evidence),
    target: {
      state: target.state,
      verified: target.verified === true,
      postcondition: target.postcondition === true,
      authoritative: target.authoritative === true,
      near: target.near === true,
    },
    near:
      evidence.near === true ||
      target.near === true ||
      target.state === "candidate" ||
      target.state === "visible" ||
      target.state === "reached",
    overshot: evidence.overshot === true,
    mutated: evidence.mutated === true,
  };
}

function normaliseScanEvidence(evidence) {
  if (
    !evidence ||
    typeof evidence !== "object" ||
    !evidence.coverage ||
    typeof evidence.coverage !== "object"
  ) {
    policyError("SCAN assess must return coverage evidence");
  }
  const coverage = evidence.coverage;
  const start = policyNumber(coverage.start, "coverage.start");
  const end = policyNumber(coverage.end, "coverage.end");
  if (end <= start) policyError("coverage.end must be greater than coverage.start");
  if (typeof evidence.end !== "boolean")
    policyError("SCAN assess must return boolean end evidence");
  return {
    source: policySourceReference(evidence.source),
    container: policyContainer(evidence),
    coverage: { start, end },
    end: evidence.end,
    mutated: evidence.mutated === true,
  };
}

function resultBase(policy, request, budgets) {
  return {
    schema: POLICY_SCHEMA,
    policy,
    window_address: request.windowAddress,
    container: request.containerId,
    budgets,
    usage: { input: 0, travel: 0, steps: 0, no_progress: 0, backtracks: 0 },
    attempts: [],
    final: null,
  };
}

function finishPolicy(result, status, reason) {
  if (result._coverage && result._config) {
    result.coverage = coverageReport(result._coverage, result._config);
    delete result._coverage;
    delete result._config;
  }
  result.status = status;
  result.reason = reason;
  return result;
}

function updateFinal(result, frame, source, evidence) {
  result.final = { frame, frame_id: frame?.id ?? null, source, evidence };
  result.source = source;
}

function seekBudgetStatus(result, targetSeen, reason) {
  return finishPolicy(
    result,
    targetSeen ? "not-reached-within-budget" : "not-yet-observed",
    reason,
  );
}

function verifyContainer(config, evidence, previousPosition = null) {
  if (evidence.container.id !== config.containerId) {
    return {
      status: "wrong-container",
      reason: `expected container ${config.containerId}, got ${evidence.container.id}`,
    };
  }
  const moved = previousPosition === null || evidence.container.position !== previousPosition;
  return { moved };
}

function commonFailure(result, report, details, context) {
  const attempt = policyAttempt(report, { ...context, error: details?.error ?? context.error });
  result.attempts.push(attempt);
  if (context.action) result.usage.steps += 1;
  return attempt;
}

export async function seekTarget(request, options = {}) {
  const config = validateSeekPolicy(request);
  const settings = policyOptions(options);
  const started = settings.now();
  const deadlineAt = started + config.deadlineMs;
  const result = resultBase("SEEK", config, {
    initial_step: config.initialStep,
    max_step: config.maxStep,
    max_input: config.maxInput,
    max_travel: config.maxTravel,
    deadline_ms: config.deadlineMs,
    max_no_progress: config.maxNoProgress,
    max_backtracks: config.maxBacktracks,
  });
  result.target = config.target;

  if (isCancelled(settings.signal))
    return finishPolicy(result, "cancelled", "cancelled before observation");
  const initial = await policyObserve(config, [], settings, deadlineAt);
  if (initial.cancelled) return finishPolicy(result, "cancelled", "cancelled during observation");
  if (initial.deadline)
    return finishPolicy(result, "deadline", "deadline expired before target evidence");
  if (initial.error) {
    commonFailure(result, null, initial, { phase: "initial", attempt: 0 });
    return finishPolicy(result, "observation-failed", initial.error);
  }
  const initialFailure = classifyPolicyReport(initial.report, false);
  if (initialFailure) {
    commonFailure(result, initial.report, initial, { phase: "initial", attempt: 0 });
    return finishPolicy(
      result,
      initialFailure,
      initialFailure === "observation-failed"
        ? "initial observation unavailable"
        : "initial observation was not usable",
    );
  }
  let bound = validateBoundFrame(config, initial.report);
  if (bound.status) {
    commonFailure(result, initial.report, bound, { phase: "initial", attempt: 0 });
    return finishPolicy(result, bound.status, bound.reason);
  }
  let initialEvidence;
  try {
    initialEvidence = normaliseSeekEvidence(
      await settings.assess({
        policy: "SEEK",
        phase: "initial",
        target: config.target,
        report: initial.report,
        step: 0,
        direction: config.direction,
        previous: null,
      }),
    );
  } catch (error) {
    commonFailure(result, initial.report, null, {
      phase: "initial",
      attempt: 0,
      error: error instanceof Error ? error.message : String(error),
    });
    return finishPolicy(result, "assessor-error", result.attempts.at(-1).error);
  }
  const initialSource = initialEvidence.source ?? bound.source;
  if (initialSource === undefined || initialSource === null) {
    commonFailure(result, initial.report, null, {
      phase: "initial",
      attempt: 0,
      error: "the assessment has no source reference",
    });
    return finishPolicy(result, "invalid-evidence", result.attempts.at(-1).error);
  }
  const initialAttempt = policyAttempt(initial.report, {
    phase: "initial",
    attempt: 0,
    source: initialSource,
    evidence: initialEvidence,
  });
  result.attempts.push(initialAttempt);
  updateFinal(result, bound.frame ?? policyFrame(initial.report), initialSource, initialEvidence);
  let sourceKey = stablePolicyKey(initialSource);
  const frameSourceKey = stablePolicyKey(bound.source);
  let position = initialEvidence.container.position;
  let frame = policyFrame(initial.report);
  let step = config.initialStep;
  let direction = config.direction;
  let previousEvidence = initialEvidence;
  let targetSeen = ["candidate", "visible", "reached"].includes(initialEvidence.target.state);
  if (initialEvidence.mutated)
    return finishPolicy(result, "mutated", "the caller reported a source mutation");
  const initialContainer = verifyContainer(config, initialEvidence);
  if (initialContainer.status)
    return finishPolicy(result, initialContainer.status, initialContainer.reason);
  if (initialEvidence.target.state === "absent")
    return finishPolicy(result, "absent", "the caller-authoritatively reported the target absent");
  if (initialEvidence.target.verified && initialEvidence.target.postcondition) {
    return finishPolicy(
      result,
      "reached",
      "the target postcondition was verified in the initial observation",
    );
  }

  while (true) {
    if (isCancelled(settings.signal))
      return finishPolicy(result, "cancelled", "cancelled before the next bounded step");
    if (settings.now() >= deadlineAt)
      return finishPolicy(result, "deadline", "deadline expired before the next bounded step");
    if (result.usage.input + step > config.maxInput)
      return seekBudgetStatus(result, targetSeen, "input budget exhausted");
    if (result.usage.travel >= config.maxTravel)
      return seekBudgetStatus(result, targetSeen, "travel budget exhausted");
    if (result.usage.steps >= MAX_POLICY_STEPS)
      return seekBudgetStatus(result, targetSeen, "bounded step limit exhausted");

    const delta = direction * step;
    const action = scrollAction(frame.id, config.at, config.axis, delta);
    const response = await policyObserve(config, [action], settings, deadlineAt);
    result.usage.input += Math.abs(delta);
    result.usage.steps += 1;
    if (response.cancelled) {
      const attempt = policyAttempt(response.report, {
        phase: "step",
        attempt: result.usage.steps,
        action,
        step,
        direction,
        error: response.error,
      });
      result.attempts.push(attempt);
      return finishPolicy(result, "cancelled", "cancelled after a step; the receipt is preserved");
    }
    if (response.error) {
      const attempt = policyAttempt(null, {
        phase: "step",
        attempt: result.usage.steps,
        action,
        step,
        direction,
        error: response.error,
      });
      result.attempts.push(attempt);
      return finishPolicy(result, response.deadline ? "deadline" : "uncertain", response.error);
    }
    const failure = classifyPolicyReport(response.report, true);
    if (failure) {
      const attempt = policyAttempt(response.report, {
        phase: "step",
        attempt: result.usage.steps,
        action,
        step,
        direction,
      });
      result.attempts.push(attempt);
      return finishPolicy(
        result,
        failure,
        failure === "refused"
          ? "CU refused the step; no input was replayed"
          : "the step or its fresh observation may be partial",
      );
    }
    const attempt = policyAttempt(response.report, {
      phase: "step",
      attempt: result.usage.steps,
      action,
      step,
      direction,
    });
    result.attempts.push(attempt);
    if (response.deadline)
      return finishPolicy(
        result,
        "deadline",
        "deadline expired after a step; its receipt is preserved",
      );
    let nextBound = validateBoundFrame(config, response.report);
    if (nextBound.status) return finishPolicy(result, nextBound.status, nextBound.reason);
    if (stablePolicyKey(nextBound.source) !== frameSourceKey) {
      return finishPolicy(
        result,
        "mutated",
        "the frame source reference changed between observations",
      );
    }
    let evidence;
    try {
      evidence = normaliseSeekEvidence(
        await settings.assess({
          policy: "SEEK",
          phase: "step",
          target: config.target,
          report: response.report,
          step,
          direction,
          previous: previousEvidence,
        }),
      );
    } catch (error) {
      attempt.error = error instanceof Error ? error.message : String(error);
      return finishPolicy(result, "assessor-error", attempt.error);
    }
    const source = evidence.source ?? nextBound.source;
    if (source === undefined || source === null) {
      attempt.error = "the assessment has no source reference";
      return finishPolicy(result, "invalid-evidence", attempt.error);
    }
    if (stablePolicyKey(source) !== sourceKey) {
      attempt.source = source;
      attempt.evidence = evidence;
      return finishPolicy(result, "mutated", "the source reference changed between observations");
    }
    const container = verifyContainer(config, evidence, position);
    attempt.source = source;
    attempt.evidence = evidence;
    attempt.frame = policyFrame(response.report);
    attempt.frame_id = attempt.frame?.id ?? null;
    if (container.status) return finishPolicy(result, container.status, container.reason);
    updateFinal(result, attempt.frame, source, evidence);
    const travel = Math.abs(evidence.container.position - position);
    result.usage.travel += travel;
    position = evidence.container.position;
    frame = attempt.frame;
    previousEvidence = evidence;
    targetSeen ||= ["candidate", "visible", "reached"].includes(evidence.target.state);
    if (evidence.mutated)
      return finishPolicy(result, "mutated", "the caller reported a source mutation");
    if (evidence.target.state === "absent")
      return finishPolicy(
        result,
        "absent",
        "the caller-authoritatively reported the target absent",
      );
    if (evidence.target.verified && evidence.target.postcondition && container.moved) {
      return finishPolicy(
        result,
        "reached",
        "the target postcondition was verified after a moved container",
      );
    }
    if (!container.moved) {
      result.usage.no_progress += 1;
      if (result.usage.no_progress >= config.maxNoProgress) {
        return seekBudgetStatus(result, targetSeen, "the expected container made no progress");
      }
    } else {
      result.usage.no_progress = 0;
    }
    if (result.usage.travel >= config.maxTravel)
      return seekBudgetStatus(result, targetSeen, "travel budget exhausted");
    if (evidence.overshot) {
      if (result.usage.backtracks >= config.maxBacktracks)
        return seekBudgetStatus(result, targetSeen, "backtracking budget exhausted");
      direction *= -1;
      result.usage.backtracks += 1;
      step = Math.max(config.initialStep, Math.floor(step / 2));
    } else if (evidence.near) {
      step = Math.max(config.initialStep, Math.floor(step / 2));
    } else {
      step = Math.min(config.maxStep, step * 2);
    }
    if (settings.now() >= deadlineAt)
      return finishPolicy(result, "deadline", "deadline expired after fresh evidence");
  }
}

function mergeCoverage(intervals) {
  const merged = [];
  for (const interval of [...intervals].sort((a, b) => a.start - b.start || a.end - b.end)) {
    const last = merged.at(-1);
    if (!last || interval.start > last.end)
      merged.push({ start: interval.start, end: interval.end });
    else last.end = Math.max(last.end, interval.end);
  }
  return merged;
}

function missingCoverage(merged, range) {
  const missing = [];
  let cursor = range.start;
  for (const interval of merged) {
    if (interval.end <= range.start || interval.start >= range.end) continue;
    const start = Math.max(interval.start, range.start);
    const end = Math.min(interval.end, range.end);
    if (start > cursor) missing.push({ start: cursor, end: start });
    cursor = Math.max(cursor, end);
  }
  if (cursor < range.end) missing.push({ start: cursor, end: range.end });
  return missing;
}

function coverageReport(state, config) {
  const merged = mergeCoverage(state.segments);
  const gaps = missingCoverage(merged, config.range);
  return {
    range: { ...config.range },
    required_overlap: config.overlap,
    segments: state.segments,
    merged,
    gaps,
    overlap_violations: state.overlapViolations,
    complete: gaps.length === 0 && state.overlapViolations.length === 0,
  };
}

function addCoverage(state, config, segment) {
  if (state.segments.length) {
    const actual = Math.max(
      ...state.segments.map((existing) =>
        Math.max(0, Math.min(existing.end, segment.end) - Math.max(existing.start, segment.start)),
      ),
    );
    if (actual < config.overlap) {
      state.overlapViolations.push({
        segment_index: state.segments.length,
        actual_overlap: actual,
        required_overlap: config.overlap,
      });
    }
  }
  state.segments.push(segment);
}

function scanIncomplete(result, reason) {
  result.coverage = coverageReport(result._coverage, result._config);
  delete result._coverage;
  delete result._config;
  return finishPolicy(result, "incomplete", reason);
}

export async function scanCoverage(request, options = {}) {
  const config = validateScanPolicy(request);
  const settings = policyOptions(options);
  const started = settings.now();
  const deadlineAt = started + config.deadlineMs;
  const result = resultBase("SCAN", config, {
    step: config.step,
    max_input: config.maxInput,
    max_travel: config.maxTravel,
    deadline_ms: config.deadlineMs,
    max_no_progress: config.maxNoProgress,
  });
  result._config = config;
  result._coverage = { segments: [], overlapViolations: [] };

  if (isCancelled(settings.signal)) return scanIncomplete(result, "cancelled before observation");
  const initial = await policyObserve(config, [], settings, deadlineAt);
  if (initial.cancelled) return scanIncomplete(result, "cancelled during observation");
  if (initial.deadline) return scanIncomplete(result, "deadline expired before coverage evidence");
  if (initial.error) {
    commonFailure(result, null, initial, { phase: "initial", attempt: 0 });
    return scanIncomplete(result, initial.error);
  }
  const initialFailure = classifyPolicyReport(initial.report, false);
  if (initialFailure) {
    commonFailure(result, initial.report, initial, { phase: "initial", attempt: 0 });
    return scanIncomplete(
      result,
      initialFailure === "observation-failed" ? "initial observation unavailable" : initialFailure,
    );
  }
  let bound = validateBoundFrame(config, initial.report);
  if (bound.status) {
    commonFailure(result, initial.report, bound, { phase: "initial", attempt: 0 });
    return finishPolicy(result, bound.status, bound.reason);
  }
  let initialEvidence;
  try {
    initialEvidence = normaliseScanEvidence(
      await settings.assess({
        policy: "SCAN",
        phase: "initial",
        report: initial.report,
        step: 0,
        direction: config.direction,
        previous: null,
      }),
    );
  } catch (error) {
    commonFailure(result, initial.report, null, {
      phase: "initial",
      attempt: 0,
      error: error instanceof Error ? error.message : String(error),
    });
    return scanIncomplete(result, result.attempts.at(-1).error);
  }
  const initialSource = initialEvidence.source ?? bound.source;
  if (initialSource === undefined || initialSource === null) {
    commonFailure(result, initial.report, null, {
      phase: "initial",
      attempt: 0,
      error: "the assessment has no source reference",
    });
    return scanIncomplete(result, result.attempts.at(-1).error);
  }
  const initialAttempt = policyAttempt(initial.report, {
    phase: "initial",
    attempt: 0,
    source: initialSource,
    evidence: initialEvidence,
  });
  result.attempts.push(initialAttempt);
  updateFinal(result, bound.frame, initialSource, initialEvidence);
  if (initialEvidence.mutated)
    return finishPolicy(result, "mutated", "the caller reported a source mutation");
  if (initialEvidence.container.id !== config.containerId) {
    return finishPolicy(
      result,
      "wrong-container",
      `expected container ${config.containerId}, got ${initialEvidence.container.id}`,
    );
  }
  if (
    initialEvidence.coverage.start < config.range.start ||
    initialEvidence.coverage.end > config.range.end
  ) {
    return finishPolicy(
      result,
      "invalid-evidence",
      "coverage interval lies outside the requested range",
    );
  }
  result._coverage.segments.push({
    start: initialEvidence.coverage.start,
    end: initialEvidence.coverage.end,
    source: initialSource,
    frame: bound.frame,
    frame_id: bound.frame.id,
  });
  result.source = initialSource;
  let sourceKey = stablePolicyKey(initialSource);
  const frameSourceKey = stablePolicyKey(bound.source);
  let position = initialEvidence.container.position;
  let frame = bound.frame;
  let previousEvidence = initialEvidence;
  result.coverage = coverageReport(result._coverage, config);
  if (initialEvidence.end) {
    const status = result.coverage.complete ? "complete" : "incomplete";
    return status === "complete"
      ? finishPolicy(result, status, "the requested range is covered with required overlap")
      : scanIncomplete(result, "end evidence arrived before complete overlap-preserving coverage");
  }

  while (true) {
    if (isCancelled(settings.signal))
      return scanIncomplete(result, "cancelled before the next bounded step");
    if (settings.now() >= deadlineAt)
      return scanIncomplete(result, "deadline expired before the next bounded step");
    if (result.usage.input + config.step > config.maxInput)
      return scanIncomplete(result, "input budget exhausted");
    if (result.usage.travel >= config.maxTravel)
      return scanIncomplete(result, "travel budget exhausted");
    if (result.usage.steps >= MAX_POLICY_STEPS)
      return scanIncomplete(result, "bounded step limit exhausted");

    const delta = config.direction * config.step;
    const action = scrollAction(frame.id, config.at, config.axis, delta);
    const response = await policyObserve(config, [action], settings, deadlineAt);
    result.usage.input += Math.abs(delta);
    result.usage.steps += 1;
    if (response.cancelled) {
      result.attempts.push(
        policyAttempt(response.report, {
          phase: "step",
          attempt: result.usage.steps,
          action,
          step: config.step,
          direction: config.direction,
          error: response.error,
        }),
      );
      return scanIncomplete(result, "cancelled after a step; the receipt is preserved");
    }
    if (response.error) {
      result.attempts.push(
        policyAttempt(null, {
          phase: "step",
          attempt: result.usage.steps,
          action,
          step: config.step,
          direction: config.direction,
          error: response.error,
        }),
      );
      return scanIncomplete(result, response.error);
    }
    const failure = classifyPolicyReport(response.report, true);
    if (failure) {
      result.attempts.push(
        policyAttempt(response.report, {
          phase: "step",
          attempt: result.usage.steps,
          action,
          step: config.step,
          direction: config.direction,
        }),
      );
      return finishPolicy(
        result,
        failure,
        failure === "refused"
          ? "CU refused the step; no input was replayed"
          : "the step or its fresh observation may be partial",
      );
    }
    const attempt = policyAttempt(response.report, {
      phase: "step",
      attempt: result.usage.steps,
      action,
      step: config.step,
      direction: config.direction,
    });
    result.attempts.push(attempt);
    if (response.deadline)
      return scanIncomplete(result, "deadline expired after a step; its receipt is preserved");
    const nextBound = validateBoundFrame(config, response.report);
    if (nextBound.status) return finishPolicy(result, nextBound.status, nextBound.reason);
    if (stablePolicyKey(nextBound.source) !== frameSourceKey) {
      return finishPolicy(
        result,
        "mutated",
        "the frame source reference changed between observations",
      );
    }
    let evidence;
    try {
      evidence = normaliseScanEvidence(
        await settings.assess({
          policy: "SCAN",
          phase: "step",
          report: response.report,
          step: config.step,
          direction: config.direction,
          previous: previousEvidence,
        }),
      );
    } catch (error) {
      attempt.error = error instanceof Error ? error.message : String(error);
      return scanIncomplete(result, attempt.error);
    }
    const source = evidence.source ?? nextBound.source;
    attempt.source = source;
    attempt.evidence = evidence;
    attempt.frame = policyFrame(response.report);
    attempt.frame_id = attempt.frame?.id ?? null;
    if (source === undefined || source === null)
      return scanIncomplete(result, "the assessment has no source reference");
    if (stablePolicyKey(source) !== sourceKey || evidence.mutated)
      return finishPolicy(
        result,
        "mutated",
        "the source reference or content changed between observations",
      );
    const container = verifyContainer(config, evidence, position);
    if (container.status) return finishPolicy(result, container.status, container.reason);
    if (evidence.coverage.start < config.range.start || evidence.coverage.end > config.range.end) {
      return finishPolicy(
        result,
        "invalid-evidence",
        "coverage interval lies outside the requested range",
      );
    }
    if (!container.moved) {
      result.usage.no_progress += 1;
    } else {
      result.usage.no_progress = 0;
      result.usage.travel += Math.abs(evidence.container.position - position);
    }
    position = evidence.container.position;
    frame = attempt.frame;
    updateFinal(result, frame, source, evidence);
    addCoverage(result._coverage, config, {
      start: evidence.coverage.start,
      end: evidence.coverage.end,
      source,
      frame,
      frame_id: frame?.id ?? null,
    });
    result.coverage = coverageReport(result._coverage, config);
    previousEvidence = evidence;
    if (result.coverage.complete && evidence.end)
      return finishPolicy(
        result,
        "complete",
        "the requested range is covered with required overlap",
      );
    if (evidence.end)
      return scanIncomplete(
        result,
        "end evidence arrived with a coverage gap or overlap violation",
      );
    if (result.usage.no_progress >= config.maxNoProgress)
      return scanIncomplete(result, "the expected container made no progress");
    if (result.usage.travel >= config.maxTravel)
      return scanIncomplete(result, "travel budget exhausted");
    if (settings.now() >= deadlineAt)
      return scanIncomplete(result, "deadline expired after fresh evidence");
  }
}

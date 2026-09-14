import assert from "node:assert/strict";
import { existsSync, lstatSync, readFileSync, readlinkSync, realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "chromium");
const manifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8"));

test("the Chromium manifest is MV3 with only the Firefox permission set", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ["activeTab", "scripting", "nativeMessaging"]);
  assert.equal(manifest.host_permissions, undefined);
  assert.equal(manifest.content_scripts, undefined);
  assert.equal(manifest.background.service_worker, "worker.js");
  assert.equal(manifest.action.default_popup, "popup.html");
  assert.match(manifest.key, /^[A-Za-z0-9+/]+=*$/);
});

test("shared files are relative links that resolve inside the extension tree", () => {
  for (const name of ["background.js", "collector.js", "popup.html", "popup.css", "popup.js"]) {
    const link = join(root, name);
    assert.equal(lstatSync(link).isSymbolicLink(), true, name);
    assert.equal(readlinkSync(link), `../${name}`, name);
    assert.equal(realpathSync(link), realpathSync(join(here, name)), name);
  }
  for (const name of [manifest.background.service_worker, manifest.action.default_popup])
    assert.equal(existsSync(join(root, name)), true, name);
});

test("the worker installs the shared controller in callback mode", () => {
  const installs = [];
  const context = {
    chrome: { marker: "chrome-api" },
    importScripts: (file) => {
      assert.equal(file, "background.js");
      context.CUBrowserExtension = { install: (api, options) => installs.push({ api, options }) };
    },
  };
  context.globalThis = context;
  vm.runInNewContext(readFileSync(join(root, "worker.js"), "utf8"), context);
  assert.equal(installs.length, 1);
  assert.equal(installs[0].api.marker, "chrome-api");
  // Objects built inside the vm context carry that context's prototypes.
  assert.deepEqual(JSON.parse(JSON.stringify(installs[0].options)), {
    respond: "callback",
    collectorFiles: ["collector.js", "chromium-collector.js"],
    acceptCollectorPorts: true,
  });
});

test("the content bootstrap installs one callback-mode collector", () => {
  const installs = [];
  const context = {
    chrome: {},
    document: {},
    CUBrowserCollector: { install: (...args) => (installs.push(args), { collector: true }) },
  };
  context.globalThis = context;
  const source = readFileSync(resolve(root, "chromium-collector.js"), "utf8");
  vm.runInNewContext(source, context);
  vm.runInNewContext(source, context);
  assert.equal(installs.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(installs[0][2])), {
    respond: "callback",
    releaseWhenDisconnected: true,
  });
});

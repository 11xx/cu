import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const html = readFileSync(new URL("../src/cu/playground.html", import.meta.url), "utf8");
function fixture() {
  return new JSDOM(html, {
    runScripts: "dangerously", url: "http://127.0.0.1/",
    beforeParse(window) { window.HTMLCanvasElement.prototype.getContext = () => ({}); },
  });
}
function set(dom, id, value) {
  const field = dom.window.document.getElementById(id);
  field.value = value;
  field.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
}
function receipt(dom) {
  return JSON.parse(dom.window.document.getElementById("receipt").textContent);
}

test("native tasks require changed fields, one apply, readiness and bottom action", async () => {
  const dom = fixture();
  try {
    const doc = dom.window.document;
    assert.equal(receipt(dom).tasks.basics, false);
    set(dom, "entry", "4242"); set(dom, "notes", "Luna was here");
    assert.equal(receipt(dom).tasks.basics, false);
    doc.getElementById("apply").click();
    assert.equal(receipt(dom).tasks.basics, true);
    assert.equal(receipt(dom).tasks.followup, false);
    doc.getElementById("delay").click();
    await new Promise(resolve => setTimeout(resolve, 400));
    assert.equal(receipt(dom).tasks.followup, false);
    doc.getElementById("bottom").click();
    assert.equal(receipt(dom).tasks.followup, true);
    doc.getElementById("apply").click();
    assert.equal(receipt(dom).tasks.basics, false);
  } finally { dom.window.close(); }
});

test("editing after an incorrect submission does not turn it into a passing task", () => {
  const dom = fixture();
  try {
    dom.window.document.getElementById("apply").click();
    set(dom, "entry", "4242"); set(dom, "notes", "Luna was here");
    assert.equal(receipt(dom).tasks.basics, false);
  } finally { dom.window.close(); }
});

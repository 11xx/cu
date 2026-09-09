/** Offline provider fixture exercising Pi's real tool and image-result path. */
import { createAssistantMessageEventStream, type AssistantMessage } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { writeFileSync } from "node:fs";

export default function (pi: ExtensionAPI) {
  const target = process.env.CU_PROBE_WINDOW;
  const receipt = process.env.CU_PROBE_REPORT;
  if (!target || !receipt) throw new Error("Set CU_PROBE_WINDOW and CU_PROBE_REPORT for the disposable fixture");
  let called = false;
  pi.registerProvider("cu-offline-fixture", {
    baseUrl: "http://localhost.invalid",
    apiKey: "offline-fixture-not-a-credential",
    api: "cu-offline-fixture",
    models: [{ id: "fixture", name: "Offline CU fixture", reasoning: false,
      input: ["text", "image"], contextWindow: 200000, maxTokens: 1024,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
    streamSimple(model, context) {
      const stream = createAssistantMessageEventStream();
      const result = context.messages.findLast(m => m.role === "toolResult" && m.toolName === "cu_observation");
      const content: AssistantMessage["content"] = called
        ? [{ type: "text", text: result?.isError ? "CU fixture failed; inspect the refusal." : "CU fixture complete: image and descriptor returned in one tool invocation." }]
        : [{ type: "toolCall", id: "cu-fixture-1", name: "cu_observation", arguments: {
          shot: ["--window", target], why: "read-only native harness image display probe" } }];
      const output: AssistantMessage = { role: "assistant", content, api: model.api, provider: model.provider,
        model: model.id, timestamp: Date.now(), stopReason: called ? "stop" : "toolUse",
        usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } };
      stream.push({ type: "start", partial: output });
      const block = content[0];
      if (block.type === "toolCall") {
        stream.push({ type: "toolcall_start", contentIndex: 0, partial: output });
        stream.push({ type: "toolcall_end", contentIndex: 0, toolCall: block, partial: output });
      } else if (block.type === "text") {
        stream.push({ type: "text_start", contentIndex: 0, partial: output });
        stream.push({ type: "text_delta", contentIndex: 0, delta: block.text, partial: output });
        stream.push({ type: "text_end", contentIndex: 0, content: block.text, partial: output });
      }
      called = true;
      stream.push({ type: "done", reason: output.stopReason, message: output });
      stream.end();
      return stream;
    },
  });
  pi.on("tool_execution_end", event => {
    if (event.toolName !== "cu_observation") return;
    const content = event.result.content;
    const report = JSON.parse(content.find(c => c.type === "text")!.text);
    const image = content.find(c => c.type === "image");
    writeFileSync(receipt, JSON.stringify({ isError: event.isError, report,
      imageBytes: image ? Buffer.from(image.data, "base64").length : 0,
      blocks: content.map(c => c.type) }));
  });
}

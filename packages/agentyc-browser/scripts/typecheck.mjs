import { readFile } from "node:fs/promises";
import { strict as assert } from "node:assert";

const declarations = await readFile(
  new URL("../src/index.d.ts", import.meta.url),
  "utf8",
);
for (const source of [
  "client.ts",
  "space.ts",
  "page.ts",
  "actions.ts",
  "waits.ts",
  "events.ts",
  "errors.ts",
  "transport.ts",
]) {
  const sourceText = await readFile(
    new URL(`../src/${source}`, import.meta.url),
    "utf8",
  );
  assert.match(
    sourceText,
    /export type/,
    `missing typed source facade: ${source}`,
  );
  assert.doesNotMatch(sourceText, /\b(?:tab|target|session|chrome|cdp)_id\b/i);
}
for (const symbol of [
  "TaskSpace",
  "Page",
  "BrowserClient",
  "LocalTransport",
  "UnknownOutcomeError",
  "ReconciliationRequiredError",
]) {
  assert.match(
    declarations,
    new RegExp(`\\b${symbol}\\b`),
    `missing declaration: ${symbol}`,
  );
}
assert.doesNotMatch(declarations, /\b(?:tab|target|session|chrome|cdp)_id\b/i);
assert.match(declarations, /batch\s*<|batch\s*\(/, "batch API is missing");
assert.match(declarations, /reconnect\s*\(/, "reconnect API is missing");
console.log("type declarations passed");

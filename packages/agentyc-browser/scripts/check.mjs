import { readFile, readdir } from "node:fs/promises";
import { strict as assert } from "node:assert";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = new URL("../src/", import.meta.url);
const files = (await readdir(root)).filter((file) => file.endsWith(".mjs"));
for (const file of files) {
  await new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      ["--check", fileURLToPath(new URL(file, root))],
      { stdio: "inherit" },
    );
    child.on("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`${file} failed syntax check`)),
    );
    child.on("error", reject);
  });
}
const declarations = await readFile(
  new URL("../src/index.d.ts", import.meta.url),
  "utf8",
);
assert.doesNotMatch(declarations, /\b(?:tab|target|session|chrome|cdp)_id\b/i);
console.log("runtime syntax and public logical-id checks passed");

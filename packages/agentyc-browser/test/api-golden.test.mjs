import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import * as api from "../src/index.mjs";

const testDirectory = dirname(fileURLToPath(import.meta.url));

test("runtime exports match the reviewed TypeScript public API golden", async () => {
  const golden = JSON.parse(
    await readFile(join(testDirectory, "golden/public-api.json"), "utf8"),
  );
  const runtimeExports = Object.keys(api).sort();
  assert.deepEqual(runtimeExports, [...golden.runtime_exports].sort());

  const declarations = await readFile(
    join(testDirectory, "../src/index.d.ts"),
    "utf8",
  );
  for (const name of runtimeExports) {
    const declaration = new RegExp(
      `\\bexport\\s+(?:(?:declare\\s+)?(?:class|function|const|type|interface)\\s+)?${name}\\b`,
    );
    assert.match(declarations, declaration, `${name} lacks a declaration export`);
  }
});

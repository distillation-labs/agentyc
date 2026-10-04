import { strict as assert } from "node:assert";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(new URL("../package.json", import.meta.url));
let compiler;
try {
  const packageRoot = dirname(dirname(require.resolve("typescript")));
  compiler = join(packageRoot, "bin", "tsc");
} catch (error) {
  if (error.code !== "MODULE_NOT_FOUND") throw error;
  console.error(
    "TypeScript typecheck unavailable: install TypeScript in this package or an ancestor workspace.",
  );
  process.exitCode = 1;
}

if (compiler) {
  const result = spawnSync(
    process.execPath,
    [
      compiler,
      "--project",
      fileURLToPath(new URL("../tsconfig.json", import.meta.url)),
    ],
    { stdio: "inherit" },
  );
  if (result.error) throw result.error;
  assert.equal(
    result.status,
    0,
    `TypeScript declaration check failed with exit status ${result.status}`,
  );
}

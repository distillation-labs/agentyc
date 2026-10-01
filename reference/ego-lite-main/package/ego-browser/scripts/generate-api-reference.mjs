import { readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { format } from "prettier";

import { publicApiMarkdown } from "../dist/src/public-api-schema.js";

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const outputPath = join(
  dirname(dirname(packageRoot)),
  "skills",
  "ego-browser",
  "references",
  "api.md",
);
const expected = await format(publicApiMarkdown(), { parser: "markdown" });

if (process.argv.includes("--check")) {
  let actual = "";
  try {
    actual = await readFile(outputPath, "utf8");
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  if (actual !== expected) {
    throw new Error(
      "skills/ego-browser/references/api.md is stale; run npm run generate:api-docs",
    );
  }
} else {
  await writeFile(outputPath, expected, "utf8");
}

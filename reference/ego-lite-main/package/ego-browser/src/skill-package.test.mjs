import test from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile, readlink } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = fileURLToPath(new URL("../../../", import.meta.url));
const bundledSkill = fileURLToPath(
  new URL("../dist/out/ego-browser/", import.meta.url),
);

test("the bundled Skill contains only publishable project resources", async () => {
  assert.deepEqual((await readdir(bundledSkill)).sort(), [
    "SKILL.md",
    "learnings",
    "references",
    "scripts",
  ]);
  assert.equal(
    await readFile(join(bundledSkill, "SKILL.md"), "utf8"),
    await readFile(join(repoRoot, "skills/ego-browser/SKILL.md"), "utf8"),
  );
});

test("project Agent and Codex entries share the canonical Skill", async () => {
  for (const directory of [".agents", ".codex"]) {
    assert.equal(
      await readlink(join(repoRoot, directory, "skills/ego-browser")),
      "../../skills/ego-browser",
    );
  }
});

import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const extensionRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(
  await readFile(join(extensionRoot, "manifest.json"), "utf8"),
);

test("production manifest declares the Chrome identity and minimal worker", async () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.name, "Agentyc");
  assert.equal(manifest.minimum_chrome_version, "125");
  assert.equal(manifest.incognito, "not_allowed");
  assert.equal(
    manifest.background.service_worker,
    "src/tab-creation-worker.mjs",
  );
  assert.equal(manifest.background.type, "module");
  assert.equal(manifest.side_panel, undefined);
  assert.equal(manifest.content_scripts, undefined);
  assert.equal(manifest.action, undefined);
  assert.deepEqual(manifest.icons, {
    16: "icons/icon-16.png",
    48: "icons/icon-48.png",
    128: "icons/icon-128.png",
  });

  for (const path of Object.values(manifest.icons)) {
    const bytes = await readFile(join(extensionRoot, path));
    assert.deepEqual(
      [...bytes.subarray(0, 8)],
      [137, 80, 78, 71, 13, 10, 26, 10],
    );
  }
});

test("production permissions match the reviewed MV3 capability boundary", () => {
  assert.deepEqual(manifest.permissions, ["nativeMessaging", "storage", "tabGroups"]);
  assert.equal(manifest.host_permissions, undefined);
  assert.equal(manifest.optional_host_permissions, undefined);
  assert.equal(manifest.optional_permissions, undefined);
});

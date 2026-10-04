import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { connect } from "../src/index.mjs";

const packageTestDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(packageTestDirectory, "../../..");
const manifestPath = join(projectRoot, "crates/agentyc/Cargo.toml");

async function startRustHost(t, stateDirectory, socketPath) {
  const child = spawn(
    "cargo",
    [
      "run",
      "--quiet",
      "--manifest-path",
      manifestPath,
      "--features",
      "test-support",
      "--bin",
      "agentyc-test-host",
      "--",
      "--state-dir",
      stateDirectory,
      "--socket-path",
      socketPath,
    ],
    { cwd: projectRoot, stdio: ["pipe", "pipe", "pipe"] },
  );
  let stdout = "";
  let stderr = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    stdout += chunk;
  });
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });

  const ready = new Promise((resolveReady, rejectReady) => {
    const timeout = setTimeout(
      () => rejectReady(new Error(`Rust host startup timed out: ${stderr}`)),
      180_000,
    );
    const poll = setInterval(() => {
      if (stdout.split(/\r?\n/).includes("READY")) {
        clearTimeout(timeout);
        clearInterval(poll);
        resolveReady();
      } else if (child.exitCode !== null) {
        clearTimeout(timeout);
        clearInterval(poll);
        rejectReady(
          new Error(`Rust host exited before readiness (${child.exitCode}): ${stderr}`),
        );
      }
    }, 20);
    child.once("error", (error) => {
      clearTimeout(timeout);
      clearInterval(poll);
      rejectReady(error);
    });
  });
  await ready;

  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      child.stdin.write("stop\n");
      await Promise.race([
        new Promise((resolveExit) => child.once("exit", resolveExit)),
        new Promise((resolveTimeout) =>
          setTimeout(() => {
            child.kill("SIGKILL");
            resolveTimeout();
          }, 5_000),
        ),
      ]);
    }
  });
}

test(
  "Node SDK speaks the Rust local protocol against one persistent broker",
  { timeout: 240_000 },
  async (t) => {
    const root = await mkdtemp(join(tmpdir(), "agentyc-sdk-rust-host-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    const stateDirectory = join(root, "state");
    const socketPath = join(root, "host.sock");
    await startRustHost(t, stateDirectory, socketPath);

    const client = await connect({ socketPath });
    t.after(() => client.close());

    const hostStatus = await client.hostStatus();
    assert.equal(hostStatus.lifecycle, "ready");

    const space = await client.createSpace("sdk-rust-host", {
      acceptSharedProfileDisclosure: true,
    });
    assert.match(space.id, /^space_/);

    const cursorResult = await client.request("events.cursor", {}, {
      mayHaveSideEffects: false,
    });
    const cursor = cursorResult.cursor;
    const wait = client.waitFor(
      { kind: "event_kind", event: "lease.changed" },
      {
        spaceId: space.id,
        after: {
          broker_epoch: cursor.broker_epoch,
          sequence: cursor.sequence,
        },
        timeoutMs: 5_000,
      },
    );
    const lease = await space.claim();
    assert.ok(lease.lease.lease_epoch > 0);
    const matched = await wait;
    assert.equal(matched.wait, "matched");

    const page = await space.newPage("main");
    assert.match(page.id, /^page_/);
    const pages = await space.listPages();
    assert.deepEqual(
      pages.map((listedPage) => [listedPage.id, listedPage.label]),
      [[page.id, "main"]],
    );

    const afterResume = await client.events({
      spaceId: space.id,
      afterEpoch: cursor.broker_epoch,
      afterSequence: cursor.sequence,
    });
    assert.equal(afterResume.resume, "accepted");
    assert.ok(afterResume.events.some((event) => event.event === "lease.changed"));

    await client.reconnect();
    assert.equal((await client.hostStatus()).lifecycle, "ready");
  },
);

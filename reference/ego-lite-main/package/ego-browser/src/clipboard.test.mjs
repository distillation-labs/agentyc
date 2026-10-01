import test from "node:test";
import assert from "node:assert/strict";

import {
  ClipboardRestoreError,
  withTemporaryClipboardContent,
  withTemporaryClipboardText,
} from "../dist/src/clipboard.js";

test("temporary clipboard text is restored after the action", async () => {
  const events = [];
  const value = await withTemporaryClipboardText(
    "temporary",
    async () => {
      events.push("action");
      return 42;
    },
    {
      async beginTransaction(text) {
        events.push(["begin", text]);
        return {
          async finish() {
            events.push("finish");
            return "restored";
          },
        };
      },
    },
  );

  assert.equal(value, 42);
  assert.deepEqual(events, [["begin", "temporary"], "action", "finish"]);
});

test("temporary clipboard content keeps text and HTML representations together", async () => {
  const content = {
    text: "A\tB",
    html: "<table><tr><td>A</td><td>B</td></tr></table>",
  };
  let prepared;

  await withTemporaryClipboardContent(content, async () => {}, {
    async beginTransaction(value) {
      prepared = value;
      return {
        async finish() {
          return "restored";
        },
      };
    },
  });

  assert.deepEqual(prepared, content);
});

test("temporary clipboard text is restored when the action throws", async () => {
  let finished = false;
  const primary = new Error("paste input failed");

  await assert.rejects(
    () =>
      withTemporaryClipboardText(
        "temporary",
        async () => {
          throw primary;
        },
        {
          async beginTransaction() {
            return {
              async finish() {
                finished = true;
                return "restored";
              },
            };
          },
        },
      ),
    (error) => error === primary,
  );
  assert.equal(finished, true);
});

test("a restore failure reports that the paste action already completed", async () => {
  await assert.rejects(
    () =>
      withTemporaryClipboardText("temporary", async () => "done", {
        async beginTransaction() {
          return {
            async finish() {
              throw new Error("pasteboard unavailable");
            },
          };
        },
      }),
    (error) => {
      assert.ok(error instanceof ClipboardRestoreError);
      assert.equal(error.code, "EGO_CLIPBOARD_RESTORE_FAILED");
      assert.equal(error.pasteCompleted, true);
      assert.match(error.message, /paste completed.*do not retry/i);
      return true;
    },
  );
});

test("an external clipboard change is respected instead of restoring stale data", async () => {
  const value = await withTemporaryClipboardText("temporary", async () => 7, {
    async beginTransaction() {
      return {
        async finish() {
          return "changed";
        },
      };
    },
  });

  assert.equal(value, 7);
});

test("clipboard transactions are serialized within one runtime", async () => {
  const events = [];
  let releaseFirst;
  const firstHold = new Promise((resolve) => {
    releaseFirst = resolve;
  });
  let markFirstStarted;
  const firstStarted = new Promise((resolve) => {
    markFirstStarted = resolve;
  });
  const beginTransaction = async (text) => {
    events.push(`begin:${text}`);
    return {
      async finish() {
        events.push(`finish:${text}`);
        return "restored";
      },
    };
  };

  const first = withTemporaryClipboardText(
    "first",
    async () => {
      events.push("action:first");
      markFirstStarted();
      await firstHold;
    },
    { beginTransaction },
  );
  await firstStarted;
  const second = withTemporaryClipboardText(
    "second",
    async () => {
      events.push("action:second");
    },
    { beginTransaction },
  );
  await Promise.resolve();
  assert.deepEqual(events, ["begin:first", "action:first"]);

  releaseFirst();
  await Promise.all([first, second]);
  assert.deepEqual(events, [
    "begin:first",
    "action:first",
    "finish:first",
    "begin:second",
    "action:second",
    "finish:second",
  ]);
});

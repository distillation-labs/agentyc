import {
  type ActionOperation,
  type ActionReceipt,
  type BrowserClient,
  type LogicalActionId,
  type LogicalSpaceId,
  PAGE_HELPER_OPERATIONS,
} from "@agentyc/browser";

declare const client: BrowserClient;
declare const spaceId: LogicalSpaceId;
declare const actionId: LogicalActionId;

const space = client.taskSpace(spaceId);
const page = space.page("main");
const receipt: Promise<ActionReceipt | unknown> = client.actionStatus(actionId);
const disclosure = {
  acceptSharedProfileDisclosure: true as const,
};
const results: Promise<{ ok: boolean }[]> = client.batch<{ ok: boolean }>([
  { method: "space.list" },
  { method: "host.status", params: {} },
]);

void client.createSpace("alpha", disclosure);
void page.create({ leaseEpoch: 1, deadlineMs: 1_000 });
void page.action(
  "click",
  { selector: "#submit" },
  {
    idempotencyKey: "action-1",
    requestId: "req_action_1",
    deadlineMs: 1_000,
  },
);
void client.reconnect();
void receipt;
void results;

void page.goto("https://example.test/", { deadlineMs: 1_000 });
void page.click("#submit");
void page.click({ elementRef: { ref_id: "ref_1" }, x: 1, y: 2 });
void page.type("#name", "agent");
void page.fill(null, "agent", { idempotencyKey: "idem-1" });
void page.press("Enter", { target: "#name", requestId: "req_press" });
void page.scroll({ deltaY: 400 });
void page.select("#choice", "b");
void page.upload("#file", { name: "report.txt" });
void page.evaluate("document.title");
void page.waitForURL(
  { prefix: "https://example.test/" },
  {
    timeoutMs: 1_000,
    after: { broker_epoch: 4, sequence: 7 },
  },
);
void page.snapshot({ mode: "focus", focusRef: "el_1", metadataOnly: true });
const helperOperation: ActionOperation = PAGE_HELPER_OPERATIONS.fill;
void helperOperation;

// @ts-expect-error Regular expressions are not part of the host wait protocol.
void page.waitForURL(/example/);
// @ts-expect-error Snapshot modes are a closed set.
void page.snapshot({ mode: "verbose" });

// @ts-expect-error Logical space IDs cannot be used as action IDs.
const invalidActionId: LogicalActionId = spaceId;
void invalidActionId;

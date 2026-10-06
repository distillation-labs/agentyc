import {
  type ActionOperation,
  type ActionReceipt,
  type BrowserClient,
  type LogicalActionId,
  type LogicalPageId,
  type LogicalSpaceId,
  PAGE_HELPER_OPERATIONS,
} from "@agentyc/browser";

declare const client: BrowserClient;
declare const spaceId: LogicalSpaceId;
declare const pageId: LogicalPageId;
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
void client.taskSpace("new-alpha", disclosure);
void space.pause({ ttl: 60_000 });
void space.handoff({ ttl: 60_000 });
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

const targetRef = {
  ref_id: "ref_1",
  element_key: "element_submit",
  space_id: spaceId,
  page_id: pageId,
  frame_id: "frame_main",
  snapshot_version: 1,
  document_generation: 1,
  navigation_generation: 1,
  refs_epoch: 1,
};
void page.goto("https://example.test/", { deadlineMs: 1_000 });
void page.issueRef("element_submit", { frameId: "frame_main" });
void page.click({ elementRef: targetRef, selector: "#submit" });
void page.click({ elementRef: targetRef, x: 1, y: 2 });
void page.type({ elementRef: targetRef, selector: "#name" }, "agent");
void page.fill({ elementRef: targetRef, selector: "#name" }, "agent", {
  idempotencyKey: "idem-1",
});
void page.press("Enter", {
  target: { elementRef: targetRef, selector: "#name" },
  requestId: "req_press",
});
void page.scroll({ deltaY: 400 });
void page.select(
  { elementRef: targetRef, selector: "#choice" },
  "b",
);
void page.upload(
  { elementRef: targetRef, selector: "#file" },
  { name: "report.txt" },
);
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

// @ts-expect-error Creating from a label requires explicit shared-profile disclosure.
void client.taskSpace("missing-disclosure");

// @ts-expect-error Logical space IDs cannot be used as action IDs.
const invalidActionId: LogicalActionId = spaceId;
void invalidActionId;

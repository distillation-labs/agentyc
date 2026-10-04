import {
  type ActionReceipt,
  type BrowserClient,
  type LogicalActionId,
  type LogicalSpaceId,
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

// @ts-expect-error Logical space IDs cannot be used as action IDs.
const invalidActionId: LogicalActionId = spaceId;
void invalidActionId;

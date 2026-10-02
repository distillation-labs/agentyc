import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { browserHint } from "../src/protocol.mjs";
import { GroupsRegistry } from "../src/groups.mjs";
import { TabsRegistry } from "../src/tabs-registry.mjs";
import { FakeChrome } from "./fake-chrome.mjs";

const proof = (kind, spaceId, pageId, leaseEpoch, suffix, extra = {}) => ({
  issued_by_host: true,
  proof_id: `${kind}-${suffix}-proof`,
  kind,
  purpose: kind,
  space_id: spaceId,
  page_id: pageId,
  lease_epoch: leaseEpoch,
  expires_at: Date.now() + 60_000,
  ...extra,
});

function makeRegistry(chrome, now = () => Date.now()) {
  const groups = new GroupsRegistry({
    chromeApi: chrome,
    hintSalt: "security",
  });
  const tabs = new TabsRegistry({
    chromeApi: chrome,
    groups,
    hintSalt: "security",
    now,
    browserSessionEpoch: 7,
  });
  return tabs;
}

async function managedPage(tabs, spaceId, pageId, rawTabId, leaseEpoch = 1) {
  tabs.bindManagedTab({
    tabId: rawTabId,
    spaceId,
    pageId,
    leaseEpoch,
    ownershipProof: {
      issued_by_host: true,
      proof_id: `claim-${pageId}-proof`,
      kind: "claim",
      space_id: spaceId,
      page_id: pageId,
      lease_epoch: leaseEpoch,
    },
  });
  return tabs.getInternalByPage(pageId);
}

test("adoption proofs are scoped, expiring, generation-bound, and single-use", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://user.test/" }],
  });
  const tabs = makeRegistry(chrome);
  await tabs.start();
  const tabHint = browserHint(1, "security");
  const adoption = proof("adoption", "space_adopt", "page_adopt", 1, "one", {
    target_generation: 1,
    tab_hint: tabHint,
    browser_session_epoch: 7,
  });
  const ticket = {
    ...adoption,
    proof_id: undefined,
    ticket_id: "ticket-adopt-one",
    expires_at: adoption.expires_at,
  };
  const result = tabs.adoptExistingTab({
    tabHint,
    spaceId: "space_adopt",
    pageId: "page_adopt",
    leaseEpoch: 1,
    ownershipProof: adoption,
    intentTicket: ticket,
  });
  assert.equal(result.page_id, "page_adopt");
  assert.throws(
    () =>
      tabs.adoptExistingTab({
        tabHint,
        spaceId: "space_adopt",
        pageId: "page_adopt",
        leaseEpoch: 1,
        ownershipProof: adoption,
        intentTicket: ticket,
      }),
    (error) => error.code === "replay_rejected",
  );

  const crossSpaceChrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://user.test/" }],
  });
  const crossSpaceTabs = makeRegistry(crossSpaceChrome);
  await crossSpaceTabs.start();
  assert.throws(
    () =>
      crossSpaceTabs.adoptExistingTab({
        tabHint,
        spaceId: "space_other",
        pageId: "page_adopt",
        leaseEpoch: 1,
        ownershipProof: { ...adoption, space_id: "space_adopt" },
        intentTicket: {
          ...ticket,
          space_id: "space_other",
          ticket_id: "ticket-adopt-two",
        },
      }),
    (error) => error.code === "permission_denied",
  );

  const expiredChrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://user.test/" }],
  });
  const expiredTabs = makeRegistry(expiredChrome);
  await expiredTabs.start();
  assert.throws(
    () =>
      expiredTabs.adoptExistingTab({
        tabHint,
        spaceId: "space_adopt",
        pageId: "page_adopt",
        leaseEpoch: 1,
        ownershipProof: {
          ...adoption,
          proof_id: "adoption-expired-proof",
          expires_at: Date.now() - 1,
        },
        intentTicket: {
          ...ticket,
          ticket_id: "ticket-adopt-expired",
          expires_at: Date.now() - 1,
        },
      }),
    (error) => error.code === "proof_expired",
  );

  const generationChrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://user.test/" }],
  });
  const generationTabs = makeRegistry(generationChrome);
  await generationTabs.start();
  generationTabs.handleAttached(1, { newWindowId: 2 });
  assert.throws(
    () =>
      generationTabs.adoptExistingTab({
        tabHint,
        spaceId: "space_adopt",
        pageId: "page_adopt",
        leaseEpoch: 1,
        ownershipProof: adoption,
        intentTicket: ticket,
      }),
    (error) => error.code === "stale_generation",
  );
});

test("cleanup proofs require a fresh live identity and reject replay, expiry, and cross-page scope", async () => {
  const chrome = new FakeChrome({
    tabs: [
      { id: 1, url: "https://agent.test/" },
      { id: 2, url: "https://agent-two.test/" },
    ],
  });
  const tabs = makeRegistry(chrome);
  await tabs.start();
  const first = await managedPage(tabs, "space_close", "page_close", 1);
  await managedPage(tabs, "space_close", "page_two", 2);
  const firstProof = proof("cleanup", "space_close", "page_close", 1, "one", {
    ownership: "agent",
    target_generation: first.targetGeneration,
    tab_hint: browserHint(1, "security"),
    browser_session_epoch: 7,
  });
  chrome.tabsData.get(1).url = "https://user-took-over.test/";
  await assert.rejects(
    () =>
      tabs.closeManagedPage({
        spaceId: "space_close",
        pageId: "page_close",
        leaseEpoch: 1,
        expectedGeneration: 1,
        cleanupProof: firstProof,
      }),
    (error) => error.code === "stale_target",
  );
  assert.deepEqual(chrome.removedTabIds, []);

  await assert.rejects(
    () =>
      tabs.closeManagedPage({
        spaceId: "space_close",
        pageId: "page_two",
        leaseEpoch: 1,
        expectedGeneration: 1,
        cleanupProof: { ...firstProof, proof_id: "cleanup-cross-page-proof" },
      }),
    (error) => error.code === "permission_denied",
  );

  chrome.tabsData.get(1).url = "https://agent.test/";
  const closed = await tabs.closeManagedPage({
    spaceId: "space_close",
    pageId: "page_close",
    leaseEpoch: 1,
    expectedGeneration: 1,
    cleanupProof: firstProof,
  });
  assert.equal(closed.closed, true);
  await assert.rejects(
    () =>
      tabs.closeManagedPage({
        spaceId: "space_close",
        pageId: "page_close",
        leaseEpoch: 1,
        expectedGeneration: 1,
        cleanupProof: firstProof,
      }),
    (error) => error.code === "replay_rejected",
  );

  const expiredChrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://agent.test/" }],
  });
  const expiredTabs = makeRegistry(expiredChrome);
  await expiredTabs.start();
  const expiredRecord = await managedPage(
    expiredTabs,
    "space_expired",
    "page_expired",
    1,
  );
  await assert.rejects(
    () =>
      expiredTabs.closeManagedPage({
        spaceId: "space_expired",
        pageId: "page_expired",
        leaseEpoch: 1,
        expectedGeneration: 1,
        cleanupProof: proof(
          "cleanup",
          "space_expired",
          "page_expired",
          1,
          "expired",
          {
            ownership: "agent",
            target_generation: expiredRecord.targetGeneration,
            tab_hint: browserHint(1, "security"),
            browser_session_epoch: 7,
            expires_at: Date.now() - 1,
          },
        ),
      }),
    (error) => error.code === "proof_expired",
  );
});

test("ordinary updates preserve lost/user-owned binding state and advance independent generations", async () => {
  const chrome = new FakeChrome({
    tabs: [{ id: 1, url: "https://agent.test/", title: "Old" }],
  });
  const tabs = makeRegistry(chrome);
  await tabs.start();
  const record = await managedPage(
    tabs,
    "space_generation",
    "page_generation",
    1,
  );
  const initial = {
    target: record.targetGeneration,
    navigation: record.navigationGeneration,
    document: record.documentGeneration,
  };
  await tabs.handleUpdated(
    1,
    { title: "New" },
    { ...chrome.tabsData.get(1), title: "New" },
  );
  assert.equal(record.targetGeneration, initial.target);
  assert.equal(record.navigationGeneration, initial.navigation);
  assert.equal(record.documentGeneration, initial.document);
  record.bindingState = "lost";
  await tabs.handleUpdated(
    1,
    { url: "https://new.test/", status: "loading" },
    { ...chrome.tabsData.get(1), url: "https://new.test/", status: "loading" },
  );
  assert.equal(record.bindingState, "lost");
  assert.equal(record.targetGeneration, initial.target);
  assert.equal(record.navigationGeneration, initial.navigation + 1);
  assert.equal(record.documentGeneration, initial.document + 1);
  record.bindingState = "user_owned";
  await tabs.handleUpdated(
    1,
    { title: "User title" },
    { ...chrome.tabsData.get(1), title: "User title" },
  );
  assert.equal(record.bindingState, "user_owned");
  const beforeAttach = record.targetGeneration;
  tabs.handleAttached(1, { newWindowId: 2 });
  assert.equal(record.targetGeneration, beforeAttach + 1);
  assert.equal(record.navigationGeneration, initial.navigation + 1);
  assert.equal(record.documentGeneration, initial.document + 1);
  tabs.handleDetached(1);
  assert.equal(record.targetGeneration, beforeAttach + 2);
});

test("production bridge files are registered as classic content scripts", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("../manifest.json", import.meta.url), "utf8"),
  );
  assert.equal(manifest.content_scripts.length, 2);
  assert.equal(
    manifest.content_scripts.some(
      (entry) =>
        entry.world === "MAIN" &&
        entry.js.includes("src/page-bridge-content.js"),
    ),
    true,
  );
  assert.equal(
    manifest.content_scripts.some(
      (entry) => entry.js.includes("src/content-bridge.js") && !entry.world,
    ),
    true,
  );
  await readFile(new URL("../src/content-bridge.js", import.meta.url), "utf8");
  await readFile(
    new URL("../src/page-bridge-content.js", import.meta.url),
    "utf8",
  );
});

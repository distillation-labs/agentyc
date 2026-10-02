// Typed source facade. The dependency-free runtime is client.mjs and the public
// declaration surface is index.d.ts so Node >=20 needs no install-time compiler.
export type {
  BrowserClient,
  ConnectOptions,
  RequestOptions,
  LogicalActionId,
  LogicalPageId,
  LogicalSpaceId,
} from "./index.d.ts";

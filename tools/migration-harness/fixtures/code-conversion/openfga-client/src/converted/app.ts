/**
 * REWRITTEN by /spicedb-dev:migrate-code (phase 4) from ../original/app.ts, which used
 * the `OpenFgaClient` source shape (code-mapping.md's "Detecting the source shape").
 * Converted construct-by-construct per code-mapping.md's call mapping table (§11.2)
 * and its six "more than a rename" mappings (§11.3):
 *
 *   1. `check` -> `checkPermission`, `write` -> `write` (via `Transaction`),
 *      `listObjects` -> `lookupResources`, `listUsers` -> `lookupSubjects`,
 *      `read` -> `readRelationships`, `expand` -> `expandPermissionTree` -- direct
 *      renames, per the call mapping table.
 *   2. `batchCheck` -> `checkPermissions`: rewritten from a correlation_id-keyed Map
 *      lookup to positional array pairing (code-mapping.md, "`batchCheck` ordering") --
 *      SpiceDB's bulk check has no correlation id anywhere on the wire.
 *   3. `expand`'s tree-walking consumer: the `leaf.tupleToUserset`-following branch
 *      (and its second `Expand` call) is DELETED, not renamed (code-mapping.md,
 *      "`expand` tree shape") -- SpiceDB's tree arrives already fully resolved in
 *      one call.
 *   4. Every `user`-typed id is encoded/decoded through shared/id_codec.ts at the API
 *      boundary (Class B, "The identifier obligation") -- shared/migration-map.json's
 *      id_encoding.types lists `user`.
 *   5. document.viewer/writer both split into a `_direct` relation plus a same-named
 *      permission (shared/migration-map.json's relation_splits) -- every write and
 *      read targets the `_direct` relation; every check targets the permission.
 *   6. Every read that follows a write earlier in THIS run (the two "after write"
 *      checks, and every call from batchCheck onward) uses `atLeastOrFull(rev)`,
 *      threaded from that write's own returned revision, instead of `minLatency()`.
 *      This is a harness-determinism fix, not a mapping correction -- see the
 *      comment at its first use below for why `minLatency()` alone is unsound here
 *      even though it is the faithful, no-more-no-less conversion of the source's
 *      UNSPECIFIED/MINIMIZE_LATENCY consistency (code-mapping.md §11.4).
 */
import { createSpiceDBClient, minLatency, atLeastOrFull, Transaction, type CheckRequest } from "@spicedb/client";
import { readFileSync } from "node:fs";
import { emit, type TupleSummary } from "../../../shared/report.ts";
import { encode, decode, type IdEncoding } from "../../../shared/id_codec.ts";

const SPICEDB_ENDPOINT = process.env.SPICEDB_ENDPOINT as string;
const SPICEDB_TOKEN = process.env.SPICEDB_TOKEN as string;

if (!SPICEDB_ENDPOINT || !SPICEDB_TOKEN) {
  throw new Error("SPICEDB_ENDPOINT and SPICEDB_TOKEN must be set in the environment");
}

const MIGRATION_MAP_PATH = new URL("../../../shared/migration-map.json", import.meta.url).pathname;
const ID_ENCODING: IdEncoding = JSON.parse(readFileSync(MIGRATION_MAP_PATH, "utf-8")).id_encoding;

const client = createSpiceDBClient(SPICEDB_ENDPOINT, SPICEDB_TOKEN, { insecure: true });

function encodeUser(id: string): string {
  return encode("user", id, ID_ENCODING);
}
function decodeUser(id: string): string {
  return decode("user", id, ID_ENCODING);
}

async function main() {
  // ------------------------------------------------------------------
  // check (was: client.check({user,relation,object}))
  // ------------------------------------------------------------------
  const bobViewer = await client.checkPermission(minLatency(), {
    resourceType: "document",
    resourceId: "1",
    permission: "viewer",
    subjectType: "user",
    subjectId: encodeUser("bob"),
  });
  emit({ op: "check", label: "check:bob-viewer", allowed: bobViewer });

  // ------------------------------------------------------------------
  // write (unconditioned) + check. was: client.write({writes:[{user,relation,object}]})
  // Targets viewer__direct, the relation shared/migration-map.json's relation_splits
  // records for document.viewer -- permissions aren't writable in SpiceDB.
  //
  // atLeastOrFull(rev), not minLatency(), for the check below -- fix round 1.
  // minLatency() IS the faithful, no-more-no-less conversion of the source's
  // UNSPECIFIED/MINIMIZE_LATENCY consistency (code-mapping.md §11.4: OpenFGA's
  // UNSPECIFIED never had read-your-writes either), so using it here would not be a
  // conversion defect. It is, however, a *harness* determinism defect: minLatency()
  // intentionally reads a quantized, possibly-stale revision by design, even against
  // a single-node `spicedb serve-testing` -- measured directly, a write-then-check
  // probe under minLatency() returned a stale `false` 1 run in 5, and this fixture's
  // comparison must not flake depending on server-side cache timing. Threading the
  // write's own returned revision through atLeastOrFull(rev) is code-mapping.md
  // §11.4's own documented option ("nearly free... once a call site already has a
  // revision in hand from a nearby write"), used here specifically so this fixture
  // measures conversion correctness, not replication timing. Do not revert this to
  // minLatency() -- every read below that follows a write earlier in this run uses
  // the same reasoning, so the same flake would reappear for all of them.
  // ------------------------------------------------------------------
  const revAfterCarolWrite = await client.write(
    new Transaction().touch({
      resourceType: "document",
      resourceId: "1",
      resourceRelation: "viewer__direct",
      subjectType: "user",
      subjectId: encodeUser("carol"),
    }),
  );
  const carolViewer = await client.checkPermission(atLeastOrFull(revAfterCarolWrite), {
    resourceType: "document",
    resourceId: "1",
    permission: "viewer",
    subjectType: "user",
    subjectId: encodeUser("carol"),
  });
  emit({ op: "check", label: "check:carol-viewer-after-write", allowed: carolViewer });

  // ------------------------------------------------------------------
  // write (conditioned) + check. was: write with `condition: {name, context}` on the
  // tuple_key. SpiceDB carries the same shape directly on the Relationship:
  // caveatName/caveatContext, stored on the relationship itself, evaluated at check
  // time -- no context needs to be threaded through the check call below.
  // Same atLeastOrFull(rev) reasoning as the write/check pair above.
  // ------------------------------------------------------------------
  const revAfterDaveWrite = await client.write(
    new Transaction().touch({
      resourceType: "document",
      resourceId: "1",
      resourceRelation: "viewer__direct",
      subjectType: "user",
      subjectId: encodeUser("dave"),
      caveatName: "is_active",
      caveatContext: { active: false },
    }),
  );
  const daveViewer = await client.checkPermission(atLeastOrFull(revAfterDaveWrite), {
    resourceType: "document",
    resourceId: "1",
    permission: "viewer",
    subjectType: "user",
    subjectId: encodeUser("dave"),
  });
  emit({ op: "check", label: "check:dave-viewer-inactive-caveat", allowed: daveViewer });

  // ------------------------------------------------------------------
  // write with a USERSET-typed subject (group:eng#member, not a plain user) + check.
  // was: client.write({writes:[{user:"group:eng#member",...}]}) -- the userset
  // subject syntax is identical on the OpenFGA side; on SpiceDB it becomes
  // subjectType/subjectId/subjectRelation on the Relationship, same as any other
  // write, with no id-codec call (group is not in id_encoding.types). A new object
  // (document:2) keeps this isolated from every other label's expected value.
  // ------------------------------------------------------------------
  const revAfterGroupWrite = await client.write(
    new Transaction().touch({
      resourceType: "document",
      resourceId: "2",
      resourceRelation: "viewer__direct",
      subjectType: "group",
      subjectId: "eng",
      subjectRelation: "member",
    }),
  );
  const anneViewerDoc2 = await client.checkPermission(atLeastOrFull(revAfterGroupWrite), {
    resourceType: "document",
    resourceId: "2",
    permission: "viewer",
    subjectType: "user",
    subjectId: encodeUser("anne"),
  });
  emit({ op: "check", label: "check:anne-viewer-document2-via-group-write", allowed: anneViewerDoc2 });

  // Every read below observes data written earlier in this run (carol/dave/the
  // group grant) -- atLeastOrFull(revAfterGroupWrite), the latest revision on hand,
  // for all of them, same reasoning as above.

  // ------------------------------------------------------------------
  // batchCheck -> checkPermissions. was: a correlation_id-keyed Map lookup
  // (code-mapping.md, "`batchCheck` ordering" -- Class C). checkPermissions returns a
  // plain, positionally-ordered boolean[] -- there is no correlation id anywhere on
  // the SpiceDB wire, so the consumer below pairs each result back to its request by
  // array position instead of a correlation-id lookup.
  // ------------------------------------------------------------------
  const batchLabels = ["req-bob-viewer", "req-anne-viewer", "req-anne-writer"];
  const batchRequests: CheckRequest[] = [
    { resourceType: "document", resourceId: "1", permission: "viewer", subjectType: "user", subjectId: encodeUser("bob") },
    { resourceType: "document", resourceId: "1", permission: "viewer", subjectType: "user", subjectId: encodeUser("anne") },
    { resourceType: "document", resourceId: "1", permission: "writer", subjectType: "user", subjectId: encodeUser("anne") },
  ];
  const batchResults = await client.checkPermissions(atLeastOrFull(revAfterGroupWrite), ...batchRequests);
  batchLabels.forEach((label, i) => {
    emit({ op: "batchCheck", label: `batchCheck:${label}`, allowed: batchResults[i] });
  });

  // ------------------------------------------------------------------
  // listObjects -> lookupResources. was: client.listObjects({type,relation,user})
  // ------------------------------------------------------------------
  const objectIds: string[] = [];
  for await (const resourceId of client.lookupResources(
    { resourceType: "document", permission: "viewer", subjectType: "user", subjectId: encodeUser("anne") },
    atLeastOrFull(revAfterGroupWrite),
  )) {
    objectIds.push(resourceId);
  }
  emit({ op: "listObjects", label: "listObjects:anne-viewer", objects: objectIds.sort() });

  // ------------------------------------------------------------------
  // listUsers -> lookupSubjects. was: client.listUsers({object,relation,user_filters})
  // Results are user-typed subject ids -- decode before they leave the boundary
  // (Class B, "The identifier obligation").
  // ------------------------------------------------------------------
  const userIds: string[] = [];
  for await (const subjectId of client.lookupSubjects(
    { resourceType: "document", resourceId: "1", permission: "viewer", subjectType: "user" },
    atLeastOrFull(revAfterGroupWrite),
  )) {
    userIds.push(decodeUser(subjectId));
  }
  emit({ op: "listUsers", label: "listUsers:document1-viewer", users: userIds.sort() });

  // ------------------------------------------------------------------
  // read -> readRelationships, with a Filter. was: client.read({object,relation}).
  // Filters on viewer__direct, the split relation -- `read` against the OpenFGA
  // `viewer` relation reads exactly the tuples this filter reads here.
  // ------------------------------------------------------------------
  const tuples: TupleSummary[] = [];
  for await (const r of client.readRelationships(
    { resourceType: "document", resourceId: "1", resourceRelation: "viewer__direct" },
    atLeastOrFull(revAfterGroupWrite),
  )) {
    tuples.push({
      subjectType: r.subjectType,
      subjectId: r.subjectType === "user" ? decodeUser(r.subjectId) : r.subjectId,
      subjectRelation: r.subjectRelation,
      conditioned: !!r.caveatName,
    });
  }
  tuples.sort((a, b) => (a.subjectId + (a.subjectRelation ?? "")).localeCompare(b.subjectId + (b.subjectRelation ?? "")));
  emit({ op: "read", label: "read:document1-viewer", tuples });

  // ------------------------------------------------------------------
  // expand -> expandPermissionTree. was: a hand-rolled tree walker that followed
  // leaf.tupleToUserset with a second Expand call. That branch is DELETED here, not
  // renamed (code-mapping.md, "`expand` tree shape") -- SpiceDB's tree has no node
  // kind corresponding to an unresolved pointer; the parent->owner arrow is already
  // walked server-side, in this one response.
  // ------------------------------------------------------------------
  const { treeRoot } = await client.expandPermissionTree(atLeastOrFull(revAfterGroupWrite), {
    resourceType: "document",
    resourceId: "1",
    permission: "writer",
  });
  const leafUsers = collectLeafUsers(treeRoot).map(decodeUser);
  emit({ op: "expand", label: "expand:document1-writer", leafUsers: leafUsers.sort() });
}

// Minimal shape covering the fields SpiceDBClient's expandPermissionTree ever
// produces for this fixture's tree -- protobuf-ES's oneof discriminated-union shape
// (`treeType: {case, value}`), not a full re-declaration of core.proto's message set.
interface TreeNode {
  treeType:
    | { case: "intermediate"; value: { children: TreeNode[] } }
    | { case: "leaf"; value: { subjects: { object: { objectId: string } }[] } }
    | { case: undefined };
}

function collectLeafUsers(node: unknown): string[] {
  const n = node as TreeNode;
  if (n.treeType.case === "intermediate") {
    return n.treeType.value.children.flatMap(collectLeafUsers);
  }
  if (n.treeType.case === "leaf") {
    return n.treeType.value.subjects.map((s) => s.object.objectId);
  }
  return [];
}

main().catch((err) => {
  console.error("FATAL:", err);
  process.exit(1);
});

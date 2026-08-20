/**
 * Fixture app exercising the `OpenFgaClient` source shape (code-mapping.md's
 * "Detecting the source shape" -- the idiomatic, flattened wrapper: camelCase inputs,
 * plain object literals, snake_case-free responses). This is the shape most
 * application code written against `@openfga/sdk` actually uses.
 *
 * Runs against a live OpenFGA server loaded with shared/model.fga and
 * shared/seed-relationships.json. Every authorization answer is printed as one line
 * of canonical JSON (shared/report.ts) so scripts/compare.mjs can diff this run
 * against the converted app's run without caring that the two SDKs' native response
 * shapes are structurally different.
 */
import { OpenFgaClient, type ClientBatchCheckSingleResponse } from "@openfga/sdk";
import { emit, type TupleSummary } from "../../../shared/report.ts";

const API_URL = process.env.FGA_API_URL || "http://localhost:28091";
const STORE_ID = process.env.FGA_STORE_ID as string;

if (!STORE_ID) {
  throw new Error("FGA_STORE_ID must be set in the environment");
}

const client = new OpenFgaClient({ apiUrl: API_URL, storeId: STORE_ID });

async function main() {
  // ------------------------------------------------------------------
  // check
  // ------------------------------------------------------------------
  const bobViewer = await client.check({ user: "user:bob", relation: "viewer", object: "document:1" });
  emit({ op: "check", label: "check:bob-viewer", allowed: !!bobViewer.allowed });

  // ------------------------------------------------------------------
  // write (unconditioned) + check
  // ------------------------------------------------------------------
  await client.write({ writes: [{ user: "user:carol", relation: "viewer", object: "document:1" }] });
  const carolViewer = await client.check({ user: "user:carol", relation: "viewer", object: "document:1" });
  emit({ op: "check", label: "check:carol-viewer-after-write", allowed: !!carolViewer.allowed });

  // ------------------------------------------------------------------
  // write (conditioned on is_active=false, i.e. an inactive share-grant) + check.
  // The condition context is carried on the tuple itself (RelationshipCondition),
  // not passed at check time -- OpenFGA evaluates it from the stored tuple.
  // ------------------------------------------------------------------
  await client.write({
    writes: [
      {
        user: "user:dave",
        relation: "viewer",
        object: "document:1",
        condition: { name: "is_active", context: { active: false } },
      },
    ],
  });
  const daveViewer = await client.check({ user: "user:dave", relation: "viewer", object: "document:1" });
  emit({ op: "check", label: "check:dave-viewer-inactive-caveat", allowed: !!daveViewer.allowed });

  // ------------------------------------------------------------------
  // write with a USERSET-typed subject (group:eng#member, not a plain user) + check.
  // Neither of the two writes above ever exercises this subject shape -- the only
  // group#member relationship in this fixture was, before this addition, static seed
  // data (shared/seed-relationships.json), never something a write() call produced.
  // A new object (document:2) keeps this isolated from every other label's expected
  // value above.
  // ------------------------------------------------------------------
  await client.write({ writes: [{ user: "group:eng#member", relation: "viewer", object: "document:2" }] });
  const anneViewerDoc2 = await client.check({ user: "user:anne", relation: "viewer", object: "document:2" });
  emit({ op: "check", label: "check:anne-viewer-document2-via-group-write", allowed: !!anneViewerDoc2.allowed });

  // ------------------------------------------------------------------
  // batchCheck -- consumer pairs results back to requests by correlation_id, the
  // pattern `OpenFgaClient.batchCheck`'s own wrapper uses internally
  // (code-mapping.md's "`batchCheck` ordering": "the idiomatic wrapper... reading
  // Object.entries(response) and looking each correlationId up in a Map"). This is
  // exactly the pairing mechanism that has no SpiceDB-side equivalent -- SpiceDB's
  // bulk check has no correlation id anywhere on the wire.
  // ------------------------------------------------------------------
  const batchResp = await client.batchCheck({
    checks: [
      { user: "user:bob", relation: "viewer", object: "document:1", correlationId: "req-bob-viewer" },
      { user: "user:anne", relation: "viewer", object: "document:1", correlationId: "req-anne-viewer" },
      { user: "user:anne", relation: "writer", object: "document:1", correlationId: "req-anne-writer" },
    ],
  });
  const byCorrelationId = new Map<string, ClientBatchCheckSingleResponse>(
    batchResp.result.map((r) => [r.correlationId, r]),
  );
  for (const label of ["req-bob-viewer", "req-anne-viewer", "req-anne-writer"]) {
    const result = byCorrelationId.get(label);
    emit({ op: "batchCheck", label: `batchCheck:${label}`, allowed: !!result?.allowed });
  }

  // ------------------------------------------------------------------
  // listObjects -- which documents can anne view?
  // ------------------------------------------------------------------
  const listObjectsResp = await client.listObjects({ type: "document", relation: "viewer", user: "user:anne" });
  const objectIds = listObjectsResp.objects.map((o) => o.split(":")[1]).sort();
  emit({ op: "listObjects", label: "listObjects:anne-viewer", objects: objectIds });

  // ------------------------------------------------------------------
  // listUsers -- which users can view document:1?
  // ------------------------------------------------------------------
  const listUsersResp = await client.listUsers({
    object: { type: "document", id: "1" },
    relation: "viewer",
    user_filters: [{ type: "user" }],
  });
  const userIds = listUsersResp.users.map((u) => u.object!.id).sort();
  emit({ op: "listUsers", label: "listUsers:document1-viewer", users: userIds });

  // ------------------------------------------------------------------
  // read -- every viewer tuple stored directly on document:1
  // ------------------------------------------------------------------
  const readResp = await client.read({ object: "document:1", relation: "viewer" });
  const tuples: TupleSummary[] = readResp.tuples
    .map((t): TupleSummary => {
      const [subjectType, rest] = t.key.user.split(":");
      const [subjectId, subjectRelation] = rest.split("#");
      return {
        subjectType,
        subjectId,
        subjectRelation: subjectRelation || undefined,
        conditioned: !!t.key.condition,
      };
    })
    .sort((a, b) => (a.subjectId + (a.subjectRelation ?? "")).localeCompare(b.subjectId + (b.subjectRelation ?? "")));
  emit({ op: "read", label: "read:document1-viewer", tuples });

  // ------------------------------------------------------------------
  // expand -- walk document:1#writer's tree by hand, following any
  // leaf.tupleToUserset pointer with a second Expand call (code-mapping.md's
  // "`expand` tree shape": OpenFGA's tree can contain an unresolved pointer to
  // another object's relation, requiring recursive client-side follow-up).
  // ------------------------------------------------------------------
  const expandResp = await client.expand({ object: "document:1", relation: "writer" });
  const leafUsers = expandResp.tree?.root ? await collectLeafUsers(expandResp.tree.root) : [];
  emit({ op: "expand", label: "expand:document1-writer", leafUsers: leafUsers.sort() });
}

// Minimal shape covering the fields this fixture's tree ever produces -- not a
// full re-declaration of apiModel.d.ts's Node type.
interface ExpandNode {
  leaf?: {
    users?: { users: string[] };
    tupleToUserset?: { tupleset: string; computed: { userset: string }[] };
  };
  union?: { nodes: ExpandNode[] };
}

async function collectLeafUsers(node: ExpandNode): Promise<string[]> {
  if (node.union) {
    const nested = await Promise.all(node.union.nodes.map(collectLeafUsers));
    return nested.flat();
  }
  if (node.leaf?.users) {
    return node.leaf.users.users.map((u) => u.split(":")[1]);
  }
  if (node.leaf?.tupleToUserset) {
    // Each `computed` entry is already resolved to a concrete `type:id#relation` --
    // OpenFGA's own /expand has already looked up the tupleset relation's current
    // tuples server-side. Follow each one with a second Expand call.
    const out: string[] = [];
    for (const c of node.leaf.tupleToUserset.computed) {
      const [obj, relation] = c.userset.split("#");
      const resp = await client.expand({ object: obj, relation });
      if (resp.tree?.root) {
        out.push(...(await collectLeafUsers(resp.tree.root)));
      }
    }
    return out;
  }
  return [];
}

main().catch((err) => {
  console.error("FATAL:", err);
  process.exit(1);
});

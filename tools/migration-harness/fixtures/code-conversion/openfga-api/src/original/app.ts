/**
 * Fixture app exercising the `OpenFgaApi` source shape (code-mapping.md's
 * "Detecting the source shape" -- the generated, un-idiomatic client: raw wire
 * shapes, `tuple_key`, `writes.tuple_keys`, `storeId` as an explicit first argument
 * on every call -- the shape `@openfga/sdk` has taken since v0.4.0, per
 * code-mapping.md's "Store ID's position is a real, version-dependent tell").
 *
 * Runs against a live OpenFGA server loaded with shared/model.fga and
 * shared/seed-relationships.json. Every authorization answer is printed as one line
 * of canonical JSON (shared/report.ts) so scripts/compare.mjs can diff this run
 * against the converted app's run.
 */
import { OpenFgaApi, Configuration } from "@openfga/sdk";
import { emit, type TupleSummary } from "../../../shared/report.ts";

const API_URL = process.env.FGA_API_URL || "http://localhost:28091";
const STORE_ID = process.env.FGA_STORE_ID as string;

if (!STORE_ID) {
  throw new Error("FGA_STORE_ID must be set in the environment");
}

const raw = new OpenFgaApi(new Configuration({ apiUrl: API_URL }));

/** `OpenFgaApi`'s raw methods can resolve to either `{body: T}` or `T` depending on
 * axios interceptor config -- code-mapping.md's own worked example unwraps the same
 * way (`rawResp.body ?? rawResp`). */
function unwrap<T>(resp: T | { body: T }): T {
  return (resp as { body?: T }).body ?? (resp as T);
}

async function main() {
  // ------------------------------------------------------------------
  // check
  // ------------------------------------------------------------------
  const checkResp = unwrap(
    await raw.check(STORE_ID, { tuple_key: { user: "user:bob", relation: "viewer", object: "document:1" } }),
  );
  emit({ op: "check", label: "check:bob-viewer", allowed: !!checkResp.allowed });

  // ------------------------------------------------------------------
  // write (unconditioned) + check
  // ------------------------------------------------------------------
  await raw.write(STORE_ID, { writes: { tuple_keys: [{ user: "user:carol", relation: "viewer", object: "document:1" }] } });
  const carolResp = unwrap(
    await raw.check(STORE_ID, { tuple_key: { user: "user:carol", relation: "viewer", object: "document:1" } }),
  );
  emit({ op: "check", label: "check:carol-viewer-after-write", allowed: !!carolResp.allowed });

  // ------------------------------------------------------------------
  // write (conditioned on is_active=false) + check
  // ------------------------------------------------------------------
  await raw.write(STORE_ID, {
    writes: {
      tuple_keys: [
        {
          user: "user:dave",
          relation: "viewer",
          object: "document:1",
          condition: { name: "is_active", context: { active: false } },
        },
      ],
    },
  });
  const daveResp = unwrap(
    await raw.check(STORE_ID, { tuple_key: { user: "user:dave", relation: "viewer", object: "document:1" } }),
  );
  emit({ op: "check", label: "check:dave-viewer-inactive-caveat", allowed: !!daveResp.allowed });

  // ------------------------------------------------------------------
  // write with a USERSET-typed subject (group:eng#member, not a plain user) + check.
  // Neither of the two writes above ever exercises this subject shape -- the only
  // group#member relationship in this fixture was, before this addition, static seed
  // data (shared/seed-relationships.json), never something a write() call produced.
  // A new object (document:2) keeps this isolated from every other label's expected
  // value above.
  // ------------------------------------------------------------------
  await raw.write(STORE_ID, { writes: { tuple_keys: [{ user: "group:eng#member", relation: "viewer", object: "document:2" }] } });
  const anneDoc2Resp = unwrap(
    await raw.check(STORE_ID, { tuple_key: { user: "user:anne", relation: "viewer", object: "document:2" } }),
  );
  emit({ op: "check", label: "check:anne-viewer-document2-via-group-write", allowed: !!anneDoc2Resp.allowed });

  // ------------------------------------------------------------------
  // batchCheck -- at this raw layer the response is a genuine map keyed by
  // correlation_id (`BatchCheckResponse.result: {[correlation_id]: BatchCheckSingleResult}`
  // -- confirmed at the protocol level, not just the idiomatic wrapper's own choice;
  // code-mapping.md's "`batchCheck` ordering" curls this exact endpoint). The
  // consumer below looks each answer up by its correlation_id key.
  // ------------------------------------------------------------------
  const batchResp = unwrap(
    await raw.batchCheck(STORE_ID, {
      checks: [
        { tuple_key: { user: "user:bob", relation: "viewer", object: "document:1" }, correlation_id: "req-bob-viewer" },
        { tuple_key: { user: "user:anne", relation: "viewer", object: "document:1" }, correlation_id: "req-anne-viewer" },
        { tuple_key: { user: "user:anne", relation: "writer", object: "document:1" }, correlation_id: "req-anne-writer" },
      ],
    }),
  );
  for (const label of ["req-bob-viewer", "req-anne-viewer", "req-anne-writer"]) {
    const result = batchResp.result?.[label];
    emit({ op: "batchCheck", label: `batchCheck:${label}`, allowed: !!result?.allowed });
  }

  // ------------------------------------------------------------------
  // listObjects -- which documents can anne view?
  // ------------------------------------------------------------------
  const listObjectsResp = unwrap(
    await raw.listObjects(STORE_ID, { type: "document", relation: "viewer", user: "user:anne" }),
  );
  const objectIds = listObjectsResp.objects.map((o) => o.split(":")[1]).sort();
  emit({ op: "listObjects", label: "listObjects:anne-viewer", objects: objectIds });

  // ------------------------------------------------------------------
  // listUsers -- which users can view document:1?
  // ------------------------------------------------------------------
  const listUsersResp = unwrap(
    await raw.listUsers(STORE_ID, {
      object: { type: "document", id: "1" },
      relation: "viewer",
      user_filters: [{ type: "user" }],
    }),
  );
  const userIds = listUsersResp.users.map((u) => u.object!.id).sort();
  emit({ op: "listUsers", label: "listUsers:document1-viewer", users: userIds });

  // ------------------------------------------------------------------
  // read -- every viewer tuple stored directly on document:1
  // ------------------------------------------------------------------
  const readResp = unwrap(await raw.read(STORE_ID, { tuple_key: { object: "document:1", relation: "viewer" } }));
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
  // leaf.tupleToUserset pointer with a second Expand call.
  // ------------------------------------------------------------------
  const expandResp = unwrap(await raw.expand(STORE_ID, { tuple_key: { object: "document:1", relation: "writer" } }));
  const leafUsers = expandResp.tree?.root ? await collectLeafUsers(expandResp.tree.root) : [];
  emit({ op: "expand", label: "expand:document1-writer", leafUsers: leafUsers.sort() });
}

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
    const out: string[] = [];
    for (const c of node.leaf.tupleToUserset.computed) {
      const [obj, relation] = c.userset.split("#");
      const resp = unwrap(await raw.expand(STORE_ID, { tuple_key: { object: obj, relation } }));
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

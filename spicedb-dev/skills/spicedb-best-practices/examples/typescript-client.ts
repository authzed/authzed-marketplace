/**
 * SpiceDB client patterns in TypeScript.
 *
 * Run with: npx ts-node typescript-client.ts
 * Requires: @authzed/authzed-node
 *   npm install @authzed/authzed-node
 */

import { v1 } from "@authzed/authzed-node";

// ---------------------------------------------------------------------------
// Client initialization
// ---------------------------------------------------------------------------

function newClient(): ReturnType<typeof v1.NewClient> {
  const endpoint = process.env.SPICEDB_ENDPOINT ?? "localhost:50051";
  const token = process.env.SPICEDB_TOKEN ?? "dev-token";

  // Development: no TLS
  return v1.NewClient(
    token,
    endpoint,
    v1.ClientSecurity.INSECURE_PLAINTEXT_CREDENTIALS
  );
  // Production: v1.ClientSecurity.SECURE
}

// ---------------------------------------------------------------------------
// Write relationship
// ---------------------------------------------------------------------------

async function writeRelationship(
  client: ReturnType<typeof v1.NewClient>,
  resourceType: string,
  resourceId: string,
  relation: string,
  subjectType: string,
  subjectId: string
): Promise<v1.ZedToken | undefined> {
  const resp = await client.writeRelationships(
    v1.WriteRelationshipsRequest.create({
      updates: [
        v1.RelationshipUpdate.create({
          operation: v1.RelationshipUpdate_Operation.TOUCH, // idempotent upsert
          relationship: v1.Relationship.create({
            resource: v1.ObjectReference.create({
              objectType: resourceType,
              objectId: resourceId,
            }),
            relation,
            subject: v1.SubjectReference.create({
              object: v1.ObjectReference.create({
                objectType: subjectType,
                objectId: subjectId,
              }),
            }),
          }),
        }),
      ],
    })
  );
  return resp.writtenAt;
}

// ---------------------------------------------------------------------------
// Check permission
// ---------------------------------------------------------------------------

async function checkPermission(
  client: ReturnType<typeof v1.NewClient>,
  resourceType: string,
  resourceId: string,
  permission: string,
  subjectType: string,
  subjectId: string,
  zedToken?: v1.ZedToken
): Promise<boolean> {
  const consistency = zedToken
    ? v1.Consistency.create({
        requirement: { oneofKind: "atLeastAsFresh", atLeastAsFresh: zedToken },
      })
    : v1.Consistency.create({
        requirement: { oneofKind: "minimizeLatency", minimizeLatency: true },
      });

  try {
    const resp = await client.checkPermission(
      v1.CheckPermissionRequest.create({
        resource: v1.ObjectReference.create({
          objectType: resourceType,
          objectId: resourceId,
        }),
        permission,
        subject: v1.SubjectReference.create({
          object: v1.ObjectReference.create({
            objectType: subjectType,
            objectId: subjectId,
          }),
        }),
        consistency,
      })
    );
    return (
      resp.permissionship ===
      v1.CheckPermissionResponse_Permissionship.HAS_PERMISSION
    );
  } catch (err) {
    // Fail-safe: deny on any error
    console.error("checkPermission error:", err);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Bulk check
// ---------------------------------------------------------------------------

interface BulkCheckItem {
  resourceType: string;
  resourceId: string;
  permission: string;
}

async function bulkCheck(
  client: ReturnType<typeof v1.NewClient>,
  subjectType: string,
  subjectId: string,
  checks: BulkCheckItem[]
): Promise<Map<string, boolean>> {
  const resp = await client.checkBulkPermissions(
    v1.CheckBulkPermissionsRequest.create({
      items: checks.map((c) =>
        v1.CheckBulkPermissionsRequestItem.create({
          resource: v1.ObjectReference.create({
            objectType: c.resourceType,
            objectId: c.resourceId,
          }),
          permission: c.permission,
          subject: v1.SubjectReference.create({
            object: v1.ObjectReference.create({
              objectType: subjectType,
              objectId: subjectId,
            }),
          }),
        })
      ),
    })
  );

  const results = new Map<string, boolean>();
  resp.pairs.forEach((pair, i) => {
    const key = `${checks[i].resourceType}:${checks[i].resourceId}#${checks[i].permission}`;
    results.set(
      key,
      pair.item?.permissionship ===
        v1.CheckPermissionResponse_Permissionship.HAS_PERMISSION
    );
  });
  return results;
}

// ---------------------------------------------------------------------------
// Lookup resources
// ---------------------------------------------------------------------------

async function lookupResources(
  client: ReturnType<typeof v1.NewClient>,
  resourceType: string,
  permission: string,
  subjectType: string,
  subjectId: string
): Promise<string[]> {
  const stream = client.lookupResources(
    v1.LookupResourcesRequest.create({
      resourceObjectType: resourceType,
      permission,
      subject: v1.SubjectReference.create({
        object: v1.ObjectReference.create({
          objectType: subjectType,
          objectId: subjectId,
        }),
      }),
      consistency: v1.Consistency.create({
        requirement: { oneofKind: "minimizeLatency", minimizeLatency: true },
      }),
    })
  );

  const ids: string[] = [];
  for await (const resp of stream) {
    ids.push(resp.resourceObjectId);
  }
  return ids;
}

// ---------------------------------------------------------------------------
// Demo
// ---------------------------------------------------------------------------

async function main() {
  const client = newClient();

  // 1. Write a relationship
  console.log("Writing relationship...");
  const token = await writeRelationship(
    client,
    "document",
    "doc-1",
    "viewer",
    "user",
    "alice"
  );
  console.log("Written at token:", token?.token.slice(0, 20));

  // 2. Check permission (read-your-writes with ZedToken)
  console.log("\nChecking permission with ZedToken...");
  const allowed = await checkPermission(
    client,
    "document",
    "doc-1",
    "view",
    "user",
    "alice",
    token
  );
  console.log("alice can view doc-1:", allowed);

  // 3. Bulk check
  console.log("\nBulk checking permissions...");
  const results = await bulkCheck(client, "user", "alice", [
    { resourceType: "document", resourceId: "doc-1", permission: "view" },
    { resourceType: "document", resourceId: "doc-2", permission: "view" },
  ]);
  results.forEach((v, k) => console.log(" ", k, "->", v));

  // 4. Lookup resources
  console.log("\nLooking up accessible documents...");
  const docs = await lookupResources(client, "document", "view", "user", "alice");
  console.log("alice can access:", docs);
}

main().catch(console.error);

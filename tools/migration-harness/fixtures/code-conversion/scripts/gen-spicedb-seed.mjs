#!/usr/bin/env node
// Reads shared/seed-relationships.json (the single source of truth for this
// fixture set's seed data, expressed once in OpenFGA vocabulary) and prints one
// `zed relationship create` argument line per relationship, applying
// shared/migration-map.json's relation_splits and id_encoding exactly the way a
// real /spicedb-dev:migrate-data run would -- so the SpiceDB-side seed is derived
// from the same source data scripts/seed-openfga.sh loads, not a second,
// independently-typed copy that could silently drift from it.
//
// Usage: node gen-spicedb-seed.mjs   (prints "<resource> <relation> <subject>" lines)
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const shared = path.join(here, "..", "shared");

const seed = JSON.parse(readFileSync(path.join(shared, "seed-relationships.json"), "utf-8"));
const map = JSON.parse(readFileSync(path.join(shared, "migration-map.json"), "utf-8"));

function encode(type, id) {
  if (id === "*") return id;
  if (map.id_encoding.mode !== "none" && map.id_encoding.types.includes(type)) {
    return Buffer.from(id, "utf-8").toString("base64url");
  }
  return id;
}

for (const rel of seed) {
  const split = map.relation_splits?.[rel.resourceType]?.[rel.relation];
  const targetRelation = split ? split.relation : rel.relation;
  const subjectId = encode(rel.subjectType, rel.subjectId);
  const subjectRef = rel.subjectRelation
    ? `${rel.subjectType}:${subjectId}#${rel.subjectRelation}`
    : `${rel.subjectType}:${subjectId}`;
  console.log(`${rel.resourceType}:${rel.resourceId} ${targetRelation} ${subjectRef}`);
}

#!/usr/bin/env node
// Compares a baseline run's output (against live OpenFGA) to a converted run's
// output (against live SpiceDB), both in the canonical JSONL shape shared/report.ts
// defines. Asserts on parsed structure, not rendered text -- each line is parsed as
// JSON and compared by its `label` key; `op`/payload fields are deep-equal-checked,
// not substring-matched, per this project's test-hygiene rule (a check that only
// ever substring-matches rendered output has shipped passing identically with and
// without the bug it claimed to catch, four times, elsewhere in this project).
//
// Usage: node compare.mjs <baseline.jsonl> <converted.jsonl>
// Exit code 0 and "COMPARISON: PASS" if every label present in both files matches
// exactly and no label is missing from either side. Exit code 1 and "COMPARISON:
// FAIL" otherwise, printing every mismatched or missing label with both sides' values.
import { readFileSync } from "node:fs";

const [baselinePath, convertedPath] = process.argv.slice(2);
if (!baselinePath || !convertedPath) {
  console.error("usage: compare.mjs <baseline.jsonl> <converted.jsonl>");
  process.exit(2);
}

function loadRecords(filePath) {
  const lines = readFileSync(filePath, "utf-8").split("\n").filter((l) => l.trim().length > 0);
  const byLabel = new Map();
  for (const line of lines) {
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue; // not every line is necessarily JSON (e.g. a stray console.error) -- skip.
    }
    if (record && typeof record === "object" && typeof record.label === "string") {
      byLabel.set(record.label, record);
    }
  }
  return byLabel;
}

function payload(record) {
  const { label, ...rest } = record;
  void label;
  return rest;
}

const baseline = loadRecords(baselinePath);
const converted = loadRecords(convertedPath);

const allLabels = new Set([...baseline.keys(), ...converted.keys()]);
const mismatches = [];

for (const label of [...allLabels].sort()) {
  const b = baseline.get(label);
  const c = converted.get(label);
  if (!b) {
    mismatches.push({ label, reason: "missing from baseline", baseline: undefined, converted: c });
    continue;
  }
  if (!c) {
    mismatches.push({ label, reason: "missing from converted", baseline: b, converted: undefined });
    continue;
  }
  const bPayload = JSON.stringify(payload(b));
  const cPayload = JSON.stringify(payload(c));
  if (bPayload !== cPayload) {
    mismatches.push({ label, reason: "value mismatch", baseline: payload(b), converted: payload(c) });
  }
}

if (mismatches.length === 0) {
  console.log(`COMPARISON: PASS (${allLabels.size} labels compared, baseline=${baselinePath}, converted=${convertedPath})`);
  process.exit(0);
}

console.log(`COMPARISON: FAIL (${mismatches.length}/${allLabels.size} labels mismatched, baseline=${baselinePath}, converted=${convertedPath})`);
for (const m of mismatches) {
  console.log(`  MISMATCH label=${m.label} reason=${m.reason}`);
  console.log(`    baseline:  ${JSON.stringify(m.baseline)}`);
  console.log(`    converted: ${JSON.stringify(m.converted)}`);
}
process.exit(1);

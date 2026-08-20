/**
 * Canonical output record shared by every fixture's original AND converted app.
 *
 * Neither the OpenFGA SDKs nor the vendored SpiceDB client emit directly-comparable
 * shapes (map vs. array, `user:bob` vs. bare `bob`, wrapped `PromiseResult` vs. plain
 * value, ...). Every app in this fixture set converts its SDK's own response into one
 * of the records below and prints it as one line of JSON -- so `scripts/compare.mjs`
 * asserts on this parsed structure, never on rendered/raw SDK output (the recurring
 * test-hygiene failure mode this project has been bitten by before: substring-matching
 * output that passes identically whether or not the thing under test is actually
 * correct). `label` is the comparison key: baseline and converted runs must emit the
 * same set of labels, and each label's payload (everything but `label`/`op`) must be
 * deep-equal for the comparison to pass.
 */

export type FixtureRecord =
  | { op: "check"; label: string; allowed: boolean }
  | { op: "batchCheck"; label: string; allowed: boolean }
  | { op: "listObjects"; label: string; objects: string[] }
  | { op: "listUsers"; label: string; users: string[] }
  | { op: "read"; label: string; tuples: TupleSummary[] }
  | { op: "expand"; label: string; leafUsers: string[] };

export interface TupleSummary {
  subjectType: string;
  subjectId: string;
  subjectRelation?: string;
  /** True if this tuple/relationship carries a condition/caveat, regardless of name or params. */
  conditioned: boolean;
}

export function emit(record: FixtureRecord): void {
  console.log(JSON.stringify(record));
}

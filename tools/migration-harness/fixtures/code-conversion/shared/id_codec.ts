/**
 * ID codec for the OpenFGA -> SpiceDB migration fixtures.
 *
 * Mirrors the codec /spicedb-dev:migrate-data (phase 3) emits, per data-mapping.md's
 * "The ID codec" section, cited by code-mapping.md's "The identifier obligation":
 * converted call sites must encode identifiers through the exact same codec module
 * the data migration wrote relationships under -- one codec, two consumers. This file
 * is that one codec; every converted fixture app imports it rather than each
 * reimplementing base64url encode/decode independently.
 *
 *   - encode(sourceType, sourceId, encoding) -> string
 *   - decode(sourceType, spicedbId, encoding) -> string (the inverse)
 *   - Mode is per source type, driven by migration-map.json's id_encoding.types --
 *     a type not listed passes through unchanged in both directions, regardless of mode.
 *   - The wildcard subject id "*" is never encoded or decoded, regardless of type.
 *   - "base64url" mode uses the standard base64url alphabet.
 *   - An empty id, or an id whose encoded form would exceed SpiceDB's 1024-character
 *     object-id limit, is a hard error at encode time -- never a silent truncation.
 */

export type IdEncodingMode = "none" | "base64url";

export interface IdEncoding {
  mode: IdEncodingMode;
  types: string[];
}

const WILDCARD = "*";
const MAX_OBJECT_ID_LENGTH = 1024;

function isEncodedType(sourceType: string, encoding: IdEncoding): boolean {
  return encoding.mode !== "none" && encoding.types.includes(sourceType);
}

/** Encode a source-system object id into its SpiceDB-side form. */
export function encode(sourceType: string, sourceId: string, encoding: IdEncoding): string {
  if (sourceId === WILDCARD) {
    return WILDCARD;
  }
  if (sourceId.length === 0) {
    throw new Error(`encode: empty id for type '${sourceType}' -- never silently passed through`);
  }
  if (!isEncodedType(sourceType, encoding)) {
    return sourceId;
  }
  if (encoding.mode !== "base64url") {
    throw new Error(`encode: unsupported id_encoding.mode '${encoding.mode}'`);
  }
  const encoded = Buffer.from(sourceId, "utf-8").toString("base64url");
  if (encoded.length > MAX_OBJECT_ID_LENGTH) {
    throw new Error(
      `encode: base64url(${sourceType}:${sourceId}) is ${encoded.length} chars, exceeding SpiceDB's ${MAX_OBJECT_ID_LENGTH}-character object-id limit -- refusing to truncate`,
    );
  }
  return encoded;
}

/** Decode a SpiceDB-side object id back into its original source-system form. */
export function decode(sourceType: string, spicedbId: string, encoding: IdEncoding): string {
  if (spicedbId === WILDCARD) {
    return WILDCARD;
  }
  if (!isEncodedType(sourceType, encoding)) {
    return spicedbId;
  }
  if (encoding.mode !== "base64url") {
    throw new Error(`decode: unsupported id_encoding.mode '${encoding.mode}'`);
  }
  return Buffer.from(spicedbId, "base64url").toString("utf-8");
}

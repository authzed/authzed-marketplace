# Schema Evolution

Guidance for evolving and migrating SpiceDB schemas in production without downtime or data loss.

---

## What You Can Do Freely

These changes are always safe -- SpiceDB will accept them with no relationship data concerns:

- **Add new definitions** (new resource types)
- **Add new relations** to existing definitions
- **Add new permissions** to existing definitions
- **Add new caveats**
- **Modify permission expressions** that only reference existing relations

---

## What Requires Care

### Removing Relations or Definitions

SpiceDB **rejects** a `WriteSchema` call that would remove a relation or definition if any
relationship data uses that relation. The error will be:

```
cannot remove relation X: existing relationships reference it
```

You must delete all relationships of that type before removing the schema element:

```bash
# 1. Delete all relationships using the relation
zed relationship delete --resource-type=document --relation=deprecated_viewer

# 2. Then apply the schema that removes the relation
zed schema write new.zed
```

---

## Safe Migration Workflow

### 1. Preview changes

```bash
zed schema diff old.zed new.zed
```

Review the diff to identify any removed relations or definitions.

### 2. For additive changes (safe)

```bash
zed schema write new.zed
```

### 3. For removals (requires data cleanup first)

```bash
# Delete relationship data for the relation being removed
zed relationship delete --resource-type=<type> --relation=<relation>

# Apply the schema
zed schema write new.zed
```

---

## Renaming Relations or Definitions

SpiceDB has no rename operation. Use this two-phase approach:

**Phase 1: Add the new name alongside the old**
```
definition document {
    relation viewer: user          // old name (keep writing new relationships here temporarily)
    relation reader: user          // new name (start writing new relationships here)
    permission view = viewer + reader
}
```

**Phase 2: Migrate relationship writes**

Update application code to write relationships under the new relation name (`reader`).
Backfill existing `viewer` relationships to `reader` if needed.

**Phase 3: Remove the old name**

Once all relationship writes use `reader` and you have confirmed no `viewer` relationships
remain, remove `viewer` from the schema:
```bash
zed relationship delete --resource-type=document --relation=viewer
zed schema write final.zed  # final.zed removes the viewer relation
```

---

## Recommended Transition Pattern

Rather than deleting old relations immediately, keep them as no-ops during the transition
period to give application code time to migrate:

```
definition document {
    relation deprecated_role: user  // no application code writes here anymore; safe to delete after cleanup
    relation role: user             // new, active relation
    permission access = role
}
```

This avoids schema rejection errors during the window when both old and new application
code versions may be running simultaneously.

---

## Key Rules

1. Additions are always safe -- deploy them any time
2. Removals require deleting relationship data first
3. Use `zed schema diff` before every schema write to spot removals
4. Prefer no-op transition relations over immediate deletes during rolling deployments
5. There is no built-in rename -- use add-migrate-delete

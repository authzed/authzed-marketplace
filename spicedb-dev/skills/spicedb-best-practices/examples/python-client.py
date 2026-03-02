"""
SpiceDB client patterns in Python.

Run with: python python-client.py
Requires: pip install authzed grpcio
"""

import os
from typing import Optional

import grpc
from authzed.api.v1 import (
    CheckBulkPermissionsRequest,
    CheckBulkPermissionsRequestItem,
    CheckPermissionRequest,
    CheckPermissionResponse,
    Client,
    Consistency,
    LookupResourcesRequest,
    ObjectReference,
    Relationship,
    RelationshipUpdate,
    SubjectReference,
    WriteRelationshipsRequest,
    ZedToken,
)
from grpcutil import bearer_token_credentials, insecure_bearer_token_credentials


# ---------------------------------------------------------------------------
# Client initialization
# ---------------------------------------------------------------------------

def new_client() -> Client:
    """Create a reusable SpiceDB client. Reuse this across requests."""
    endpoint = os.environ.get("SPICEDB_ENDPOINT", "localhost:50051")
    token = os.environ.get("SPICEDB_TOKEN", "dev-token")

    # Development: no TLS
    return Client(endpoint, insecure_bearer_token_credentials(token))
    # Production: bearer_token_credentials(token) with TLS endpoint


# ---------------------------------------------------------------------------
# Write relationship
# ---------------------------------------------------------------------------

def write_relationship(
    client: Client,
    resource_type: str,
    resource_id: str,
    relation: str,
    subject_type: str,
    subject_id: str,
) -> ZedToken:
    """Write a relationship and return the ZedToken for read-your-writes consistency."""
    resp = client.WriteRelationships(WriteRelationshipsRequest(
        updates=[RelationshipUpdate(
            operation=RelationshipUpdate.OPERATION_TOUCH,  # idempotent upsert
            relationship=Relationship(
                resource=ObjectReference(object_type=resource_type, object_id=resource_id),
                relation=relation,
                subject=SubjectReference(
                    object=ObjectReference(object_type=subject_type, object_id=subject_id)
                ),
            ),
        )]
    ))
    return resp.written_at


# ---------------------------------------------------------------------------
# Check permission
# ---------------------------------------------------------------------------

def check_permission(
    client: Client,
    resource_type: str,
    resource_id: str,
    permission: str,
    subject_type: str,
    subject_id: str,
    zed_token: Optional[ZedToken] = None,
) -> bool:
    """
    Check if a subject has permission on a resource.
    Fail-safe: returns False on any error (deny by default).

    Pass zed_token (from a prior write) for read-your-writes consistency.
    """
    if zed_token is not None:
        consistency = Consistency(at_least_as_fresh=zed_token)
    else:
        consistency = Consistency(minimize_latency=True)

    try:
        resp = client.CheckPermission(CheckPermissionRequest(
            resource=ObjectReference(object_type=resource_type, object_id=resource_id),
            permission=permission,
            subject=SubjectReference(
                object=ObjectReference(object_type=subject_type, object_id=subject_id)
            ),
            consistency=consistency,
        ))
        return resp.permissionship == CheckPermissionResponse.PERMISSIONSHIP_HAS_PERMISSION
    except grpc.RpcError as e:
        # Fail-safe: deny on any error
        print(f"checkPermission error: {e}")
        return False


# ---------------------------------------------------------------------------
# Bulk check
# ---------------------------------------------------------------------------

def bulk_check(
    client: Client,
    subject_type: str,
    subject_id: str,
    checks: list[dict],  # [{"resource_type": ..., "resource_id": ..., "permission": ...}]
) -> dict[str, bool]:
    """
    Check multiple permissions in one call.
    Always prefer this over looping check_permission.
    """
    items = [
        CheckBulkPermissionsRequestItem(
            resource=ObjectReference(object_type=c["resource_type"], object_id=c["resource_id"]),
            permission=c["permission"],
            subject=SubjectReference(
                object=ObjectReference(object_type=subject_type, object_id=subject_id)
            ),
        )
        for c in checks
    ]

    resp = client.CheckBulkPermissions(CheckBulkPermissionsRequest(items=items))

    results = {}
    for i, pair in enumerate(resp.pairs):
        key = f"{checks[i]['resource_type']}:{checks[i]['resource_id']}#{checks[i]['permission']}"
        results[key] = (
            pair.item.permissionship == CheckPermissionResponse.PERMISSIONSHIP_HAS_PERMISSION
        )
    return results


# ---------------------------------------------------------------------------
# Lookup resources
# ---------------------------------------------------------------------------

def lookup_resources(
    client: Client,
    resource_type: str,
    permission: str,
    subject_type: str,
    subject_id: str,
) -> list[str]:
    """Find all resource IDs of a given type that the subject can access."""
    responses = client.LookupResources(LookupResourcesRequest(
        resource_object_type=resource_type,
        permission=permission,
        subject=SubjectReference(
            object=ObjectReference(object_type=subject_type, object_id=subject_id)
        ),
        consistency=Consistency(minimize_latency=True),
    ))
    return [resp.resource_object_id for resp in responses]


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    client = new_client()

    # 1. Write a relationship
    print("Writing relationship...")
    token = write_relationship(client, "document", "doc-1", "viewer", "user", "alice")
    print(f"Written at token: {token.token[:20]}...")

    # 2. Check permission (read-your-writes with ZedToken)
    print("\nChecking permission with ZedToken...")
    allowed = check_permission(
        client, "document", "doc-1", "view", "user", "alice", zed_token=token
    )
    print(f"alice can view doc-1: {allowed}")

    # 3. Bulk check
    print("\nBulk checking permissions...")
    results = bulk_check(client, "user", "alice", [
        {"resource_type": "document", "resource_id": "doc-1", "permission": "view"},
        {"resource_type": "document", "resource_id": "doc-2", "permission": "view"},
    ])
    for key, value in results.items():
        print(f"  {key} -> {value}")

    # 4. Lookup resources
    print("\nLooking up accessible documents...")
    docs = lookup_resources(client, "document", "view", "user", "alice")
    print(f"alice can access: {docs}")

"""
SpiceDB Permission Check Load Test

Usage:
    python load_test.py

Environment:
    SPICEDB_ENDPOINT  SpiceDB gRPC endpoint (default: localhost:50051)
    SPICEDB_TOKEN     Token (default: test-token)
    LOAD_TEST_N       Number of iterations (default: 100)

Requires:
    pip install authzed grpcio
"""

import os
import statistics
import time

import grpc
from authzed.api.v1 import (
    CheckPermissionRequest,
    Client,
    Consistency,
    ObjectReference,
    SubjectReference,
    WriteRelationshipsRequest,
    RelationshipUpdate,
    Relationship,
    WriteSchemaRequest,
)
from grpcutil import bearer_token_credentials


def setup_client() -> Client:
    endpoint = os.environ.get("SPICEDB_ENDPOINT", "localhost:50051")
    token = os.environ.get("SPICEDB_TOKEN", "test-token")
    return Client(
        endpoint,
        grpc.local_channel_credentials(),
        interceptors=[bearer_token_credentials(token)],
    )


def setup_test_data(client: Client) -> None:
    """Write a schema and a relationship to check against."""
    client.WriteSchema(WriteSchemaRequest(
        schema="""
            definition user {}
            definition document {
                relation owner: user
                permission view = owner
            }
        """
    ))
    client.WriteRelationships(WriteRelationshipsRequest(
        updates=[
            RelationshipUpdate(
                operation=RelationshipUpdate.OPERATION_TOUCH,
                relationship=Relationship(
                    resource=ObjectReference(object_type="document", object_id="doc-1"),
                    relation="owner",
                    subject=SubjectReference(
                        object=ObjectReference(object_type="user", object_id="alice")
                    ),
                ),
            )
        ]
    ))


def run_load_test(client: Client, n: int) -> list[float]:
    """
    Run N permission checks and return per-check latencies in milliseconds.
    Uses minimize_latency consistency (default for reads) to reflect production behavior.
    """
    resource = ObjectReference(object_type="document", object_id="doc-1")
    subject = SubjectReference(
        object=ObjectReference(object_type="user", object_id="alice")
    )

    latencies: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        client.CheckPermission(CheckPermissionRequest(
            resource=resource,
            permission="view",
            subject=subject,
            # No consistency override = minimize_latency (default for reads)
        ))
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)

    return latencies


def report(latencies: list[float]) -> None:
    latencies_sorted = sorted(latencies)
    n = len(latencies)
    p50 = latencies_sorted[int(n * 0.50)]
    p95 = latencies_sorted[int(n * 0.95)]
    p99 = latencies_sorted[int(n * 0.99)]
    mean = statistics.mean(latencies)
    total = sum(latencies)

    print(f"\n{'='*40}")
    print(f"  Load Test Results ({n} iterations)")
    print(f"{'='*40}")
    print(f"  Mean:  {mean:.2f} ms")
    print(f"  p50:   {p50:.2f} ms")
    print(f"  p95:   {p95:.2f} ms   (target: <10 ms)")
    print(f"  p99:   {p99:.2f} ms")
    print(f"  Total: {total:.0f} ms")
    print(f"{'='*40}\n")

    if p95 > 10:
        print(f"WARNING: p95 latency {p95:.2f} ms exceeds 10 ms target.")
        print("Consider: caching, bulk checks (CheckBulkPermissions), or connection pooling.")


if __name__ == "__main__":
    n = int(os.environ.get("LOAD_TEST_N", "100"))

    client = setup_client()
    print(f"Setting up test data...")
    setup_test_data(client)

    print(f"Running {n} permission checks...")
    latencies = run_load_test(client, n)
    report(latencies)

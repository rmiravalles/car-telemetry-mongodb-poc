#!/usr/bin/env python3
import argparse
import os
from typing import Any

from azure.identity import DefaultAzureCredential
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.auth_oidc import OIDCCallback, OIDCCallbackContext, OIDCCallbackResult


class AzureManagedIdentityOIDCCallback(OIDCCallback):
    def __init__(self, credential: DefaultAzureCredential, scope: str) -> None:
        self._credential = credential
        self._scope = scope

    def fetch(self, _: OIDCCallbackContext) -> OIDCCallbackResult:
        access_token = self._credential.get_token(self._scope)
        return OIDCCallbackResult(access_token=access_token.token)


def _env_or_arg(value: str | None, env_name: str, default: str | None = None) -> str:
    if value:
        return value
    env_value = os.getenv(env_name)
    if env_value:
        return env_value
    if default is not None:
        return default
    raise ValueError(f"Missing required value. Pass argument or set {env_name}")


def _build_client(uri: str, oidc_scope: str) -> MongoClient[Any]:
    if "authmechanism=mongodb-oidc" in uri.lower():
        credential = DefaultAzureCredential()
        callback = AzureManagedIdentityOIDCCallback(credential=credential, scope=oidc_scope)
        return MongoClient(
            uri,
            authMechanism="MONGODB-OIDC",
            authMechanismProperties={"OIDC_CALLBACK": callback},
        )

    return MongoClient(uri)


def bootstrap_indexes(uri: str, database: str, collection: str, oidc_scope: str) -> None:
    client = _build_client(uri=uri, oidc_scope=oidc_scope)
    coll = client[database][collection]

    created = []
    created.append(
        coll.create_index(
            [("eventId", ASCENDING)],
            name="ux_eventId",
            unique=True,
            sparse=True,
        )
    )
    created.append(
        coll.create_index(
            [("vehicleId", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_vehicle_ts",
        )
    )
    created.append(
        coll.create_index(
            [("location", "2dsphere")],
            name="idx_location_2dsphere",
        )
    )
    created.append(
        coll.create_index(
            [("status", ASCENDING), ("timestamp", DESCENDING)],
            name="idx_status_ts",
        )
    )

    print(f"Indexes ensured for {database}.{collection}:")
    for name in created:
        print(f"- {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap MongoDB indexes for telemetry queries")
    parser.add_argument("--uri", help="MongoDB connection string")
    parser.add_argument("--database", help="MongoDB database name")
    parser.add_argument("--collection", help="MongoDB collection name")
    parser.add_argument(
        "--oidc-scope",
        default=os.getenv("MONGODB_OIDC_SCOPE", "https://management.azure.com/.default"),
        help="OIDC token scope for MONGODB-OIDC",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    uri = _env_or_arg(args.uri, "MONGODB_URI")
    database = _env_or_arg(args.database, "MONGODB_DATABASE", "telemetry")
    collection = _env_or_arg(args.collection, "MONGODB_COLLECTION", "vehicle_state")

    bootstrap_indexes(
        uri=uri,
        database=database,
        collection=collection,
        oidc_scope=args.oidc_scope,
    )

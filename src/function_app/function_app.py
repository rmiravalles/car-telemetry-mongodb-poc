import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, List

import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient
from pymongo import MongoClient
from pymongo.auth_oidc import OIDCCallback, OIDCCallbackContext, OIDCCallbackResult

app = func.FunctionApp()


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise ValueError(f"Missing required environment variable: {name}")
    return value


class AzureManagedIdentityOIDCCallback(OIDCCallback):
    def __init__(self, credential: DefaultAzureCredential, scope: str) -> None:
        self._credential = credential
        self._scope = scope

    def fetch(self, _: OIDCCallbackContext) -> OIDCCallbackResult:
        access_token = self._credential.get_token(self._scope)
        return OIDCCallbackResult(access_token=access_token.token)


def _build_data_lake_client() -> DataLakeServiceClient:
    credential = DefaultAzureCredential()
    account_url = _env("DATA_LAKE_ACCOUNT_URL")
    return DataLakeServiceClient(account_url=account_url, credential=credential)


def _build_mongodb_client() -> MongoClient[Any]:
    credential = DefaultAzureCredential()
    mongodb_uri = _env("MONGODB_URI")
    oidc_scope = os.getenv("MONGODB_OIDC_SCOPE", "https://management.azure.com/.default")

    callback = AzureManagedIdentityOIDCCallback(credential=credential, scope=oidc_scope)
    return MongoClient(
        mongodb_uri,
        authMechanism="MONGODB-OIDC",
        authMechanismProperties={"OIDC_CALLBACK": callback},
    )


data_lake_client = _build_data_lake_client()
mongo_client = _build_mongodb_client()


def _append_raw_event(payload: dict[str, Any], raw_event_json: str) -> None:
    file_system = _env("DATA_LAKE_FILE_SYSTEM")
    now = datetime.now(timezone.utc)
    vehicle_id = str(payload.get("vehicleId", "unknown"))
    event_id = str(payload.get("eventId", now.strftime("%Y%m%d%H%M%S%f")))

    path = (
        f"raw/year={now:%Y}/month={now:%m}/day={now:%d}/hour={now:%H}/"
        f"vehicle={vehicle_id}/{event_id}.json"
    )

    file_client = data_lake_client.get_file_client(file_system=file_system, file_path=path)
    file_client.create_file()
    file_client.append_data(data=raw_event_json.encode("utf-8"), offset=0, length=len(raw_event_json))
    file_client.flush_data(len(raw_event_json))


def _upsert_to_mongodb(payload: dict[str, Any]) -> None:
    database_name = _env("MONGODB_DATABASE")
    collection_name = _env("MONGODB_COLLECTION")
    collection = mongo_client[database_name][collection_name]

    telemetry_doc = {
        "eventId": payload.get("eventId"),
        "vehicleId": payload.get("vehicleId"),
        "timestamp": payload.get("timestamp"),
        "speedKph": payload.get("speedKph"),
        "rpm": payload.get("rpm"),
        "engineTempC": payload.get("engineTempC"),
        "fuelLevelPct": payload.get("fuelLevelPct"),
        "location": {
            "type": "Point",
            "coordinates": [payload.get("longitude"), payload.get("latitude")],
        },
        "odometerKm": payload.get("odometerKm"),
        "status": payload.get("status"),
        "receivedAt": datetime.now(timezone.utc),
    }

    event_id = telemetry_doc.get("eventId")
    if event_id:
        collection.replace_one({"eventId": event_id}, telemetry_doc, upsert=True)
    else:
        collection.insert_one(telemetry_doc)


@app.function_name(name="process_vehicle_telemetry")
@app.event_hub_message_trigger(
    arg_name="events",
    event_hub_name="%EVENT_HUB_NAME%",
    connection="EventHubConnection",
    consumer_group="%EVENT_HUB_CONSUMER_GROUP%",
    cardinality="many",
)
def process_vehicle_telemetry(events: List[func.EventHubEvent]) -> None:
    processed = 0
    failed = 0

    for event in events:
        try:
            raw_payload = event.get_body().decode("utf-8")
            payload = json.loads(raw_payload)

            _append_raw_event(payload=payload, raw_event_json=raw_payload)
            _upsert_to_mongodb(payload=payload)
            processed += 1
        except Exception:
            failed += 1
            logging.exception("Failed to process telemetry event")

    logging.info("Telemetry batch processed. success=%s failed=%s", processed, failed)

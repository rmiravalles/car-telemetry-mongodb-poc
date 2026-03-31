#!/usr/bin/env python3
import argparse
import json
import random
import time
import uuid
from datetime import datetime, timezone

from azure.eventhub import EventData, EventHubProducerClient
from azure.identity import DefaultAzureCredential


def _generate_vehicle_state(vehicle_id: str, base_lat: float, base_lon: float, tick: int) -> dict:
    speed = max(0.0, random.gauss(72.0, 18.0))
    rpm = int(max(700, random.gauss(2400, 650)))
    fuel = max(0.0, 95.0 - (tick * random.uniform(0.02, 0.08)))
    engine_temp = min(125.0, max(75.0, random.gauss(96.0, 6.0)))
    odometer = 10000 + tick * (speed / 3600)

    return {
        "eventId": str(uuid.uuid4()),
        "vehicleId": vehicle_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "speedKph": round(speed, 2),
        "rpm": rpm,
        "engineTempC": round(engine_temp, 2),
        "fuelLevelPct": round(fuel, 2),
        "latitude": round(base_lat + random.uniform(-0.01, 0.01), 6),
        "longitude": round(base_lon + random.uniform(-0.01, 0.01), 6),
        "odometerKm": round(odometer, 3),
        "status": "active" if speed > 1 else "idle",
    }


def run_simulator(namespace: str, event_hub_name: str, vehicle_count: int, interval: float, duration: int) -> None:
    credential = DefaultAzureCredential()
    producer = EventHubProducerClient(
        fully_qualified_namespace=namespace,
        eventhub_name=event_hub_name,
        credential=credential,
    )

    vehicle_ids = [f"car-{i:03d}" for i in range(1, vehicle_count + 1)]
    start = time.time()
    tick = 0

    print(f"Sending telemetry to {namespace}/{event_hub_name} for {duration}s...")

    with producer:
        while (time.time() - start) < duration:
            batch = producer.create_batch()
            for vehicle_id in vehicle_ids:
                payload = _generate_vehicle_state(vehicle_id, 40.4168, -3.7038, tick)
                body = json.dumps(payload)
                batch.add(EventData(body=body))
            producer.send_batch(batch)

            tick += 1
            print(f"Sent batch #{tick} with {len(vehicle_ids)} records")
            time.sleep(interval)

    print("Simulation completed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vehicle telemetry simulator for Azure Event Hubs")
    parser.add_argument("--namespace", required=True, help="Event Hubs namespace FQDN (e.g. myns.servicebus.windows.net)")
    parser.add_argument("--event-hub", default="vehicle-telemetry", help="Event Hub name")
    parser.add_argument("--vehicle-count", type=int, default=5, help="Number of vehicles simulated in each batch")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between batches")
    parser.add_argument("--duration", type=int, default=60, help="Total duration in seconds")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_simulator(
        namespace=args.namespace,
        event_hub_name=args.event_hub,
        vehicle_count=args.vehicle_count,
        interval=args.interval,
        duration=args.duration,
    )

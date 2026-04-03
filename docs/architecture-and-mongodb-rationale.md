# Real-Time Car Telemetry on Azure — Architecture & MongoDB Rationale

## 1. Overview

This document describes a proof-of-concept (PoC) pipeline for real-time vehicle telemetry processing on Azure and explains the role MongoDB Atlas plays in it, including a comparison with alternative storage approaches.

---

## 2. Architecture

### 2.1 High-Level Flow

```
[Vehicle / Simulator]
        │  AMQP over TLS
        ▼
[Azure Event Hubs]          ← partitioned, high-throughput ingestion
        │  Event Hub trigger (batch)
        ▼
[Azure Function App]        ← stateless, auto-scaling processing
        │
        ├──► [Azure Data Lake Gen2]   ← immutable raw event archive (Parquet/JSON)
        │
        └──► [MongoDB Atlas]          ← low-latency operational queries
```

### 2.2 Components

| Component | Azure Resource | Purpose |
|---|---|---|
| **Ingestion** | Event Hubs Standard (4 partitions, auto-inflate up to 4 TUs) | Durable message buffer; decouples producers from consumers |
| **Processing** | Azure Function App — Linux Consumption, Python 3.11 | Stateless, event-driven; scales to zero when idle |
| **Raw archive** | Azure Data Lake Gen2 (HNS-enabled Storage Account) | Immutable, partitioned landing zone for audit and batch analytics |
| **Operational store** | MongoDB Atlas (M10+) | Low-latency reads, flexible queries, geospatial support |
| **Observability** | Application Insights + Log Analytics | Distributed tracing, function metrics, log-based alerting |

### 2.3 Security Model

All authentication between Azure resources is **identity-based** — no passwords or connection-string secrets are embedded in application code or deployed configuration:

- The Function App runs with a **system-assigned managed identity**.
- The identity is granted `Azure Event Hubs Data Receiver` (RBAC) on the Event Hubs namespace and `Storage Blob Data Contributor` on the storage account — both assigned directly in Bicep.
- MongoDB Atlas authenticates the identity via **Workload Identity Federation / MONGODB-OIDC**: the Function acquires a short-lived Entra ID token (`DefaultAzureCredential`) and presents it to Atlas as an OIDC bearer token. No MongoDB username or password is ever stored.
- The telemetry simulator uses the same `DefaultAzureCredential` pattern, relying on `az login` locally or a managed identity in hosted environments.
- All transport is TLS 1.2+. Public blob access and FTP are explicitly disabled in the Bicep definition.

### 2.4 Data Model

Each telemetry event is stored in MongoDB as a single document:

```json
{
  "eventId": "8c8ab4e8-aa61-4f49-ab3d-5f2a2a4864f0",
  "vehicleId": "car-001",
  "timestamp": "2026-04-01T16:12:22.194113+00:00",
  "speedKph": 76.12,
  "rpm": 2310,
  "engineTempC": 95.6,
  "fuelLevelPct": 74.18,
  "location": {
    "type": "Point",
    "coordinates": [-3.706994, 40.418121]
  },
  "odometerKm": 10023.431,
  "status": "active",
  "receivedAt": "2026-04-01T16:12:22.801000+00:00"
}
```

The `location` field is stored as a **GeoJSON Point**, enabling native geospatial queries. The Function upserts documents by `eventId`, making event delivery idempotent.

### 2.5 Indexes

Four indexes are bootstrapped by `scripts/bootstrap_mongodb_indexes.py`:

| Index name | Fields | Purpose |
|---|---|---|
| `ux_eventId` | `eventId` (unique, sparse) | Idempotent upserts; deduplication |
| `idx_vehicle_ts` | `vehicleId` ASC, `timestamp` DESC | Per-vehicle history queries |
| `idx_location_2dsphere` | `location` (2dsphere) | Proximity / geofencing queries |
| `idx_status_ts` | `status` ASC, `timestamp` DESC | Fleet-wide status dashboards |

---

## 3. Real-World Device-to-Cloud Path

The simulator used in this PoC (`simulate_vehicle_data.py`) runs as a backend Python process and authenticates directly with Event Hubs using `DefaultAzureCredential`. In a production deployment, in-vehicle telematics hardware would follow a different path:

```
[ECU / Telematics Control Unit]
        │  MQTT or AMQP over TLS 1.2+
        ▼
[Azure IoT Hub]          ← per-device identity, D2C + C2D messaging
        │  built-in routing
        ▼
[Azure Event Hubs]       ← same processing pipeline from here onward
```

**Device authentication options (strongest first):**

| Mechanism | Description |
|---|---|
| **TPM + DPS** | Private key is non-exportable, generated inside the vehicle's Trusted Platform Module. Azure IoT Hub Device Provisioning Service handles registration. Strongest option; standard in automotive-grade hardware. |
| **X.509 client certificates** | Device presents a cert signed by a CA registered with IoT Hub. Private key stored in a secure enclave. Preferred for production fleet management. |
| **SAS tokens (time-limited)** | HMAC-SHA256 signed token scoped to a specific device, expiring in minutes to hours. Simpler but requires secure key storage and a rotation strategy. |

Additional hardening for production:
- **Private endpoints** on Event Hubs and Storage — function-to-service traffic stays on the Azure backbone.
- **VNet integration + NAT gateway** for the Function App — gives a stable, known outbound IP for MongoDB Atlas IP allowlisting, replacing the variable consumption-plan IPs.
- **Payload signing (JWS)** — the Function verifies that payloads were not tampered with in transit.
- **TLS certificate pinning** on the telematics unit — prevents MITM on cellular networks.
- **Dead-letter and retry strategy** — currently absent; needed before production use.

---

## 4. Why MongoDB Atlas for This Workload

### 4.1 Document Model Matches Telemetry Naturally

Vehicle telemetry events are self-contained records with varying sensor sets across vehicle models and firmware versions. A document model stores each event as a single, schema-flexible JSON document. Adding a new sensor field (e.g. `batteryVoltage` for EVs) requires no `ALTER TABLE`, no migration, and no downtime — the new field simply appears in new documents and can be indexed on demand.

### 4.2 Native Geospatial Support

The `location` field is stored as a **GeoJSON Point** and backed by a `2dsphere` index. This unlocks queries that are otherwise expensive or impossible to express in tabular stores:

```js
// All active vehicles within 5 km of a given point
db.vehicle_state.find({
  location: { $nearSphere: { $geometry: { type: "Point", coordinates: [-3.7038, 40.4168] }, $maxDistance: 5000 } },
  status: "active"
})
```

No external geospatial service or extension is needed.

### 4.3 Compound and Composite Query Patterns

The index set supports the core operational access patterns with a single round trip each:

- Latest reading for a specific vehicle: compound index on `(vehicleId, timestamp DESC)`
- All currently idle vehicles: compound index on `(status, timestamp DESC)`
- Vehicles near a point of interest: `2dsphere` index on `location`
- Deduplication on ingest: unique sparse index on `eventId`

### 4.4 Atlas Aggregation Pipeline

MongoDB's aggregation pipeline enables complex analytical queries directly on the operational store, without moving data to a separate analytics layer for moderate volumes:

```js
// Average speed per vehicle in the last hour
db.vehicle_state.aggregate([
  { $match: { timestamp: { $gte: ISODate("...") } } },
  { $group: { _id: "$vehicleId", avgSpeed: { $avg: "$speedKph" } } },
  { $sort: { avgSpeed: -1 } }
])
```

### 4.5 Atlas Search (Full-Text and Vector)

Atlas Search (built on Apache Lucene, co-located with the data) can be layered on top of the same collection for free-text log search or, with vector embeddings, for semantic queries — for example, finding events that match a natural-language description of an anomaly pattern.

### 4.6 Workload Identity Federation — No Secret Sprawl

The `MONGODB-OIDC` authentication mechanism used here means:

- No MongoDB username or password in environment variables or Key Vault.
- Token lifetime is short (minutes); the `AzureManagedIdentityOIDCCallback` fetches a fresh Entra token on each authentication handshake.
- Access can be revoked instantly by removing the federated identity mapping in Atlas, without rotating any application secret.

### 4.7 Atlas Time Series Collections (Growth Path)

For very high event volumes, the `vehicle_state` collection can be migrated to an [Atlas Time Series collection](https://www.mongodb.com/docs/manual/core/timeseries-collections/) with `timeField: "timestamp"` and `metaField: "vehicleId"`. This provides:

- Automatic columnar compression optimised for sequential time-ordered writes.
- 10–20× reduction in storage compared with standard documents at scale.
- Specialised query optimisations for range scans over time.

---

## 5. Comparison with Alternative Storage Solutions

### 5.1 Azure Cosmos DB (NoSQL API)

| Dimension | MongoDB Atlas | Cosmos DB NoSQL |
|---|---|---|
| **Data model** | Document (BSON) | Document (JSON) |
| **Query language** | MQL + aggregation pipeline | SQL-like (proprietary) |
| **Geospatial** | Native 2dsphere, rich operators | Basic geospatial via ST_ functions |
| **Authentication** | MONGODB-OIDC (Entra ID / Workload Identity) | Managed identity on data plane (GA) |
| **Pricing model** | Instance-based (M10+); predictable at steady load | Request Unit (RU/s)-based; can spike unexpectedly |
| **Portability** | Runs on-prem, multi-cloud, Atlas | Azure-only |
| **Full-text / vector search** | Atlas Search (co-located, Lucene-based) | Azure AI Search (separate service) |
| **Aggregation** | Rich pipeline with $lookup, $facet, $bucket | Limited cross-partition aggregation |
| **OIDC / no-password auth** | Yes, via MONGODB-OIDC | Yes, via Entra ID data plane RBAC |

Cosmos DB is a strong choice if the team is Azure-native, wants SLA-backed 99.999% availability, and can predict RU consumption. MongoDB Atlas is preferable when portability, richer query capabilities, or an existing MongoDB skill set matter.

### 5.2 Azure SQL / PostgreSQL (Relational)

| Dimension | MongoDB Atlas | Azure SQL / PostgreSQL |
|---|---|---|
| **Schema flexibility** | Schema-less; new fields added freely | Fixed schema; migrations required |
| **Geospatial** | Native 2dsphere on any field | PostGIS (PostgreSQL) or spatial types (SQL) |
| **Time-series optimisation** | Atlas Time Series collections | TimescaleDB extension (PostgreSQL) or partition tables |
| **Scaling write throughput** | Horizontal sharding (Atlas) | Vertical + read replicas; sharding complex |
| **JSON handling** | Native | JSONB (PostgreSQL) or JSON column (SQL) |
| **Operational complexity** | Managed SaaS | Managed PaaS, but schema management overhead |

Relational databases are excellent when the data has a well-defined, stable schema and strong ACID transactional guarantees are required across multiple entities. Telemetry events are append-oriented, schema-evolving, and do not require multi-document ACID transactions, making relational stores a poor fit without significant adaptation.

### 5.3 InfluxDB / Azure Data Explorer (Purpose-Built Time-Series)

| Dimension | MongoDB Atlas | InfluxDB / ADX |
|---|---|---|
| **Primary model** | Operational document + time-series option | Purpose-built time-series |
| **Ingestion throughput** | High (Atlas sharding) | Very high (ADX ingestion pipelines) |
| **Query language** | MQL / aggregation pipeline | Flux (InfluxDB) / KQL (ADX) |
| **Geospatial** | Native | Limited (ADX has basic geo functions) |
| **Operational queries** | Full document retrieval, upsert, deduplication | Primarily analytical / read-heavy |
| **Combined operational + analytical** | Single cluster | Requires separate stack for operational reads |
| **Cost at PoC scale** | M10 ~$60/month | ADX minimum cluster ~$400+/month |

ADX and InfluxDB excel at ingesting and querying billions of time-ordered metrics with sub-second analytical response times. If the primary use case shifts from "look up the current state of vehicle X" to "compute the average RPM across the fleet for the last 30 days", a dedicated time-series engine becomes attractive. At PoC and small-to-medium fleet scale, Atlas covers both operational and moderate analytical needs in a single service.

### 5.4 Databricks

MongoDB Atlas and Databricks solve fundamentally different problems and are often used together rather than as alternatives.

**MongoDB Atlas** is an **operational database** — it serves live application traffic with single-digit millisecond latency for indexed reads. **Databricks** is an **analytical compute platform** — it processes large volumes of data in bulk using distributed Apache Spark, suited for fleet-wide historical analysis and ML model training.

| Dimension | MongoDB Atlas | Databricks |
|---|---|---|
| **Primary role** | Operational / transactional database | Analytical / data engineering platform |
| **Query latency** | Milliseconds (point reads, indexed) | Seconds to minutes (batch / analytical) |
| **Data model** | Documents (BSON/JSON), flexible schema | Tables (Delta Lake / Parquet), columnar |
| **Query language** | MQL, aggregation pipeline | SQL, PySpark, Scala Spark |
| **Write pattern** | Random writes, upserts, real-time inserts | Bulk appends, batch overwrites |
| **Read pattern** | Individual record lookup, low latency | Full or partial table scans over billions of rows |
| **Scaling model** | Horizontal sharding (Atlas) | Distributed Spark clusters, auto-scaling compute |
| **Schema** | Schema-flexible; fields added without migration | Delta Lake adds schema evolution on top of Parquet |
| **ML / AI** | Atlas Vector Search, basic aggregations | Full MLflow, Feature Store, AutoML, notebooks |
| **Storage** | Managed by Atlas (WiredTiger) | Open format (Delta Lake on ADLS/S3/GCS) — data files owned by the user |
| **Cost model** | Instance-based (M10+, always-on) | Compute billed per-second; clusters shut down when idle |
| **Geospatial** | Native 2dsphere operators | Via Sedona / Mosaic extensions |
| **ACID transactions** | Multi-document ACID | ACID on Delta Lake tables |

In a production version of this pipeline both would typically coexist:

```
[Event Hubs]
     │
     ▼
[Azure Function]
     │
     ├──► MongoDB Atlas          ← live queries: "show me car-007's last reading"
     │                              dashboards, geofencing alerts, operator UI
     │
     └──► ADLS Gen2 (raw JSON)
               │
               ▼
          [Databricks]           ← nightly model training, fleet-wide analytics,
                                    e.g. average brake wear across 50,000 vehicles
                                    grouped by route type over 6 months
```

MongoDB handles the **online** (OLTP-like) workload; Databricks handles the **offline** (OLAP / ML) workload against the raw archive in ADLS. In short: MongoDB answers *"what is happening right now with this vehicle?"*; Databricks answers *"what patterns have emerged across the whole fleet over the last year?"*

### 5.5 Azure Table Storage / Redis Cache

These are unsuitable as a primary store for this workload:

- **Table Storage** has no geospatial support, no aggregation, and a flat key-value model that forces denormalization for every additional query pattern.
- **Redis** is an in-memory cache; it is valuable as a read-through layer in front of Atlas but cannot serve as a durable, queryable store for telemetry history.

---

## 6. Summary

MongoDB Atlas is well-suited to this workload because:

1. **The document model is a natural fit** for self-describing, schema-evolving telemetry events.
2. **Native geospatial support** covers proximity and geofencing queries without additional services.
3. **A single cluster covers both operational and moderate analytical needs**, simplifying the infrastructure footprint at small-to-medium scale.
4. **Workload Identity Federation (MONGODB-OIDC)** eliminates password-based secrets, aligning with the identity-first security model of the rest of the Azure stack.
5. **Atlas Time Series collections** provide a clear, low-friction upgrade path for high-volume scenarios.
6. **Multi-cloud portability** avoids lock-in — the same driver code and schema work on Atlas running on Azure, AWS, or GCP, or on a self-hosted MongoDB cluster.

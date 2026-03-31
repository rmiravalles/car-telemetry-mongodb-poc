# Deployment Plan

## Status
Approved

## Objective
Build a PoC for real-time vehicle telemetry where telemetry is ingested through Azure Event Hubs, processed by Azure Functions, stored as raw records in Azure Data Lake Gen2, and written to MongoDB Atlas for real-time queries.

## Architecture
1. Telemetry producer publishes JSON events to Azure Event Hubs.
2. Azure Function (Event Hub trigger) consumes events using managed identity.
3. Function writes raw JSON records to ADLS Gen2 using managed identity.
4. Function writes processed records to MongoDB Atlas using OIDC federation with managed identity.

## Provisioned Azure Resources
- Azure Storage Account (Data Lake Gen2 enabled)
- Blob container/file system for raw telemetry
- Event Hubs Namespace
- Event Hub for telemetry stream
- Azure Functions Consumption Plan (Linux)
- Azure Function App with system-assigned managed identity
- Application Insights + Log Analytics workspace
- RBAC role assignments for Function managed identity

## Security and Identity
- Event Hubs access from Function uses identity-based connection (`EventHubConnection__fullyQualifiedNamespace`).
- Data Lake access from Function uses `DefaultAzureCredential` and `Storage Blob Data Contributor` role.
- MongoDB access uses `MONGODB-OIDC` with token acquisition from managed identity.

## Out of Scope
- CI/CD pipeline
- Production hardening (private endpoints, firewall restrictions, VNet integration)
- Atlas provisioning (assumed existing cluster)

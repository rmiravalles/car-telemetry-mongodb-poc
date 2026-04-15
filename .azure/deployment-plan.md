# Deployment Plan

## Status
Pending approval

## Objective
Extend the existing PoC deployment with an Azure Databricks workspace in the same resource group so analytics workloads can read the raw telemetry stored in ADLS Gen2. The Databricks-to-storage integration will use ADLS mount points authenticated with a Microsoft Entra service principal.

## Architecture
1. Telemetry producer publishes JSON events to Azure Event Hubs.
2. Azure Function (Event Hub trigger) consumes events using managed identity.
3. Function writes raw JSON records to ADLS Gen2 using managed identity.
4. Function writes processed records to MongoDB Atlas using OIDC federation with managed identity.
5. Azure Databricks reads the raw telemetry zone in ADLS Gen2 for batch analytics and ML workflows.
6. Databricks accesses ADLS through a mount point backed by a service principal that is granted RBAC on the storage account.

## Planned Bicep Changes
- Add Azure Databricks workspace resources in the existing resource group.
- Add deployment parameters for Databricks naming and SKU.
- Add deployment parameters for an existing Microsoft Entra service principal used by Databricks mounts.
- Grant the service principal `Storage Blob Data Contributor` on the storage account so the mount can access the ADLS file system.
- Add outputs needed by Databricks mount configuration, including workspace URL, storage account name, file system name, and ABFS source URI.

## Documentation Changes
- Update the README deployment section with the new Databricks parameters.
- Document the post-deployment Databricks mount steps because the mount itself is a workspace runtime operation, not an ARM/Bicep resource.

## Security and Identity
- Event Hubs access from Function uses identity-based connection (`EventHubConnection__fullyQualifiedNamespace`).
- Data Lake access from Function uses `DefaultAzureCredential` and `Storage Blob Data Contributor` role.
- MongoDB access uses `MONGODB-OIDC` with token acquisition from managed identity.
- Databricks storage access will use an existing Microsoft Entra service principal supplied to the deployment as parameters; the deployment will only grant Azure RBAC and emit mount configuration values.

## Assumptions
- The Databricks mount service principal already exists in Microsoft Entra ID.
- The deployment will receive the service principal application (client) ID, object ID, and tenant ID as parameters.
- The client secret for that service principal will be stored in Databricks secrets after deployment and will not be embedded in Bicep parameters or outputs.

## Out of Scope
- CI/CD pipeline
- Production hardening (private endpoints, firewall restrictions, VNet integration)
- Atlas provisioning (assumed existing cluster)
- Automated creation of the Databricks mount itself inside the workspace control plane

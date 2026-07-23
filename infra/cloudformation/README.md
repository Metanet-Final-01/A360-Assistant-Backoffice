# A360 Assistant Ops CloudFormation

This directory contains the deployment-oriented CloudFormation baseline for the
Backoffice/Ops runtime.

## Repository Contracts

The templates use ports and health checks verified from the repository:

| Service | Container source | Port | Health |
|---|---|---:|---|
| Ops Backend | `ops-server/backend/Dockerfile` | `8100` | `/health` |
| Ops Streamlit | `ops-server/frontend/Dockerfile` | `8501` | `/_stcore/health` |
| RAG Server | `rag-server/Dockerfile` | `8200` | `/health` |

Values that are not present in the repository are parameters, not hard-coded
assumptions: image URI, custom domain/certificate, GitHub registry credentials,
database URL secrets, Bonsai/OpenSearch credentials, and main service backend URL.

## Stack Order

1. `network-stack.yml`
2. `data-stack.yml`
3. `application-stack.yml`
4. `access-stack.yml` after VPN certificates are prepared

The data stack creates isolated RDS security groups. The application stack adds
ingress rules from the Ops instance security group so database access remains
tied to application identities instead of broad CIDR rules.

## Architecture

- Public subnets host the Internet Gateway and NAT Gateway.
- Private application/backoffice subnets host container EC2 instances.
- Private database subnets are isolated and have no default route to NAT or IGW.
- Ops UI is exposed only through an internal ALB intended for Client VPN users.
- RAG Server remains private and is reached by Ops Backend/Streamlit through the
  container network on the EC2 host.
- RDS is split into Service, Observability, and RAG instances.
- S3 keeps evaluation/artifact data with public access blocked and Retain policy.
- EventBridge Scheduler sends RAG ingest messages to SQS; workers consume from SQS.

## Deployment

```bash
cd infra/cloudformation
./scripts/validate.sh
./scripts/deploy-network.sh
./scripts/deploy-data.sh
./scripts/deploy-application.sh
./scripts/deploy-access.sh
```

The deploy scripts require `aws` and `jq`.

The parameter JSON files intentionally contain `TODO` placeholders for values
that must come from the target AWS account or image publishing workflow.

## Secrets

Do not put secret values in parameter files. Create Secrets Manager entries first
and pass ARNs:

- `ObservabilityDatabaseUrlSecretArn`
- `RagDatabaseUrlSecretArn`
- optional `ServiceDatabaseUrlSecretArn`
- optional `RagServiceTokenSecretArn`
- optional `GhcrTokenSecretArn`

RDS master credentials are managed by RDS with `ManageMasterUserPassword`. Runtime
roles such as `ops_service_reader`, `observability_reader`, and `rag_app` should be
created by the database bootstrap/migration path after RDS creation.

## Notes

- EC2 SSH and public RDS access are intentionally absent.
- IMDSv2 is required.
- Images should use immutable Git commit SHA tags, not `latest`.
- Stack deletion snapshots RDS and retains S3 artifacts.

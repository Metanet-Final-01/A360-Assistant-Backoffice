# A360 Assistant Ops CloudFormation

Ops 운영 배포용 CloudFormation입니다.

이 레포에서는 `ops-stack.yml`만 배포합니다.

## Validate

```bash
cd infra/cloudformation
./scripts/validate.sh
```

## Deploy

```bash
cd infra/cloudformation
./scripts/deploy-ops.sh
```

`parameters/ops-dev.json`의 `TODO` 값은 배포 환경에 맞게 채워야 합니다.

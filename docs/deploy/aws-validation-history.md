# AWS Validation Work History

Date: 2026-06-19

Branch: `archive/aws-validation-history`

This branch records the AWS ECS Fargate validation work for `open-domain-mcp`.
The AWS environment was created to prove the deployment path, not to keep a
long-running production environment.

## Summary

- Built Docker packaging for the FastAPI + React dashboard application.
- Added GitHub Actions deployment workflow using GitHub OIDC into AWS.
- Provisioned a validation environment in `ap-east-2`.
- Verified HTTPS routing for `https://opendomain.bwtseng.com`.
- Verified GitHub Actions can build, push to ECR, render an ECS task definition,
  and deploy to ECS Fargate.
- Replaced the initial RDS instance with an encrypted RDS instance after review.
- Deleted the live AWS validation environment to avoid ongoing AWS charges.

## Key Commits

- `e29de2f` - AWS ECS deployment design
- `de45f61` - AWS ECS deployment implementation plan
- `2a12c90` - baseline test isolation fix
- `a096dda` - Docker image packaging
- `722abab` - Docker runtime hardening
- `f4790a7` - initial GitHub Actions workflow
- `ae03094` - GitHub Actions permission/ref hardening
- `cb9ed66` - AWS foundation outputs
- `92a776c` - AWS runtime outputs and task definition
- `beca9ee` - AWS ECS deployment runbook
- `ce06897` - merged latest `origin/main`
- `e9c6ffb` - moved ECS database to encrypted RDS

## Validated Architecture

- Region: `ap-east-2`
- Hostname: `opendomain.bwtseng.com`
- ECS cluster: `open-domain-mcp`
- ECS service: `open-domain-mcp-web`
- ECR repository: `open-domain-mcp`
- ALB: internet-facing HTTPS ALB with HTTP to HTTPS redirect
- Persistence: EFS mounted at `/data/opendomain`
- Database: MariaDB RDS, private, deletion protection enabled during validation
- Database secret: stored in AWS Secrets Manager, not committed
- GitHub auth: OIDC role scoped to `repo:b5336789/open-domain-mcp:ref:refs/heads/main`

## Validation Evidence

- Local Python tests: `187 passed, 3 skipped`
- Local frontend build: `npm ci && npm run build` succeeded
- GitHub Actions run `27823379658`: succeeded for commit `ce06897`
- GitHub Actions run `27824631284`: succeeded for commit `e9c6ffb`
- Health route during validation:

```json
{"status":"ok"}
```

## Important Fixes During Validation

### ECR image architecture

The first local ECR image was built on Apple Silicon and only had `linux/arm64`.
The ECS task definition required `X86_64`, so Fargate failed with:

```text
CannotPullContainerError: image Manifest does not contain descriptor matching platform 'linux/amd64'
```

The fix was to push an amd64 image with:

```bash
docker buildx build --platform linux/amd64 -t "$ECR_URI:latest" --push .
```

GitHub Actions later built and deployed the image successfully on GitHub-hosted
Linux runners.

### GitHub OIDC trust policy

The first trust policy string was corrupted by shell parsing and did not match
the intended repository ref. It was corrected to:

```text
repo:b5336789/open-domain-mcp:ref:refs/heads/main
```

### RDS storage encryption

The initial RDS instance was unencrypted. Because RDS encryption cannot be
enabled in place, it was replaced by:

1. Creating a snapshot from the initial DB.
2. Copying the snapshot with RDS encryption enabled.
3. Restoring a new DB instance from the encrypted snapshot.
4. Updating ECS to use the encrypted DB endpoint.
5. Deleting the original unencrypted DB and migration snapshots.

Final validated DB identifier:

```text
open-domain-mcp-db-enc
```

## Live Teardown Status

Status: completed on 2026-06-19.

Deleted or disabled:

- ECS service, cluster, task definition revisions, and CloudWatch log group
- ALB, listeners, and target group
- Route 53 `opendomain.bwtseng.com` alias
- ACM certificate and validation CNAME
- RDS MariaDB instance, DB subnet group, Secrets Manager secret, and manual
  migration snapshots
- EFS mount targets, access point, and file system
- ECR repository
- ECS/GitHub IAM roles and GitHub OIDC provider
- Security groups, subnets, route table, internet gateway, and VPC
- GitHub Actions AWS deployment variables
- GitHub Actions `Deploy to AWS ECS` workflow, disabled manually

Teardown verification showed:

- No RDS instances or manual snapshots matching `open-domain-mcp`
- No EFS file system matching the validation file system
- No ECR repository named `open-domain-mcp`
- No ALB or target group for the validation service
- No validation VPC
- No Route 53 records containing `opendomain.bwtseng.com`
- No ACM certificate for the validation hostname
- No Secrets Manager secret for the RDS password
- No deployment IAM roles or GitHub OIDC provider
- No active ECS task definitions for `open-domain-mcp-web`

# AWS ECS Fargate + GitHub Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy `open-domain-mcp` to AWS ECS Fargate in `ap-east-2` with GitHub Actions CI/CD, EFS persistence, RDS MariaDB, ALB HTTPS, Route 53 DNS, and GitHub OIDC auth.

**Architecture:** Package the React dashboard into the Python FastAPI image, push images to ECR, and run the app as an ECS Fargate service behind an HTTPS ALB. AWS infrastructure is created with AWS CLI and documented; the repository stores Docker, CI/CD, task definition, and operating docs, but no Terraform/CDK.

**Tech Stack:** Python 3.11, FastAPI, Vite/React, Docker, GitHub Actions, AWS CLI, ECR, ECS Fargate, EFS, RDS MariaDB, ALB, ACM, Route 53, IAM OIDC, Secrets Manager, CloudWatch Logs.

---

## Scope Check

This plan implements one deployable production-style environment. It includes repository artifacts and AWS resource provisioning because they are coupled by ECS task definition values, OIDC role ARN, EFS IDs, RDS endpoint, and ECR repository URI. It does not introduce Terraform, CDK, CloudFormation, multi-environment promotion, app authentication, or a cloud vector database.

## File Structure

- Create `Dockerfile`: multi-stage app image; builds `web/` and runs `opendomainmcp-web`.
- Create `.dockerignore`: keeps local state, caches, logs, dependencies, and git metadata out of the Docker build context.
- Create `.github/workflows/deploy-aws.yml`: CI/CD workflow for tests, frontend build, ECR push, ECS task render, and ECS service deploy.
- Create `deploy/aws/task-definition.json`: ECS task definition file with real AWS IDs after provisioning.
- Create `docs/deploy/aws-ecs-fargate.md`: runbook with resource names, deployment variables, verification, rollback, and teardown order.
- Create `docs/deploy/aws-outputs.env`: non-secret AWS outputs used for local follow-up commands. This file contains resource IDs and ARNs only; it must not contain passwords or API keys.

---

### Task 1: Baseline Verification

**Files:**
- Read: `pyproject.toml`
- Read: `web/package.json`
- Read: `web/vite.config.ts`
- Read: `src/opendomainmcp/api/app.py`

- [ ] **Step 1: Verify git state**

Run:

```bash
git status --short
```

Expected: no output.

- [ ] **Step 2: Verify Python tests pass before deployment edits**

Run:

```bash
python -m pytest -q
```

Expected: tests pass. If integration tests requiring live MariaDB are skipped, keep the skip output visible in the terminal notes.

- [ ] **Step 3: Verify frontend build path**

Run:

```bash
cd web
npm ci
npm run build
cd ..
test -f src/opendomainmcp/api/static/index.html
```

Expected: `npm run build` succeeds and `src/opendomainmcp/api/static/index.html` exists.

- [ ] **Step 4: Verify local deployment tools are installed**

Run:

```bash
aws --version
docker version
gh --version
jq --version
```

Expected: all four commands print versions. If `gh` is not authenticated, run:

```bash
gh auth status
```

Expected: authenticated for GitHub account with access to `b5336789/open-domain-mcp`.

---

### Task 2: Add Docker Packaging

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Create `Dockerfile`**

Use `apply_patch` to add:

```dockerfile
FROM node:20-bookworm-slim AS web-builder

WORKDIR /app

COPY web/package*.json web/
RUN cd web && npm ci

COPY web web
COPY src src
RUN cd web && npm run build

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ODM_WEB_HOST=0.0.0.0 \
    ODM_WEB_PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src src
COPY --from=web-builder /app/src/opendomainmcp/api/static src/opendomainmcp/api/static

RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/opendomain \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000

CMD ["opendomainmcp-web"]
```

- [ ] **Step 2: Create `.dockerignore`**

Use `apply_patch` to add:

```gitignore
.git
.github
.pytest_cache
.mypy_cache
.ruff_cache
.venv
venv
__pycache__
*.py[cod]
*.egg-info
.DS_Store
.env
.env.*
.opendomain
server.log
web/node_modules
web/dist
src/opendomainmcp/api/static
docs/superpowers
```

- [ ] **Step 3: Build the image locally**

Run:

```bash
docker build -t open-domain-mcp:local .
```

Expected: build succeeds.

- [ ] **Step 4: Verify the container health route starts without MariaDB**

Run:

```bash
CONTAINER_ID=$(docker run --rm -d -p 18000:8000 open-domain-mcp:local)
sleep 5
curl -fsS http://127.0.0.1:18000/api/health
docker stop "$CONTAINER_ID"
```

Expected:

```json
{"status":"ok"}
```

- [ ] **Step 5: Commit Docker packaging**

Run:

```bash
git add Dockerfile .dockerignore
git commit -m "build: add Docker image for ECS deployment"
```

Expected: commit succeeds.

---

### Task 3: Add GitHub Actions Deployment Workflow

**Files:**
- Create: `.github/workflows/deploy-aws.yml`

- [ ] **Step 1: Create workflow directory**

Run:

```bash
mkdir -p .github/workflows
```

- [ ] **Step 2: Create `.github/workflows/deploy-aws.yml`**

Use `apply_patch` to add:

```yaml
name: Deploy to AWS ECS

on:
  push:
    branches:
      - main
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

env:
  AWS_REGION: ${{ vars.AWS_REGION }}
  ECR_REPOSITORY: ${{ vars.ECR_REPOSITORY }}
  ECS_CLUSTER: ${{ vars.ECS_CLUSTER }}
  ECS_SERVICE: ${{ vars.ECS_SERVICE }}
  ECS_TASK_DEFINITION: ${{ vars.ECS_TASK_DEFINITION }}
  ECS_CONTAINER_NAME: ${{ vars.ECS_CONTAINER_NAME }}

jobs:
  test-build-deploy:
    runs-on: ubuntu-latest

    steps:
      - name: Check out source
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Python dependencies
        run: python -m pip install -e ".[dev]"

      - name: Run Python tests
        run: python -m pytest -q

      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: web/package-lock.json

      - name: Build frontend
        working-directory: web
        run: |
          npm ci
          npm run build

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.AWS_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Log in to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build, tag, and push image
        id: build-image
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          IMAGE_TAG: ${{ github.sha }}
        run: |
          IMAGE_URI="$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG"
          docker build -t "$IMAGE_URI" -t "$ECR_REGISTRY/$ECR_REPOSITORY:latest" .
          docker push "$IMAGE_URI"
          docker push "$ECR_REGISTRY/$ECR_REPOSITORY:latest"
          echo "image=$IMAGE_URI" >> "$GITHUB_OUTPUT"

      - name: Render ECS task definition
        id: render-task
        uses: aws-actions/amazon-ecs-render-task-definition@v1
        with:
          task-definition: ${{ env.ECS_TASK_DEFINITION }}
          container-name: ${{ env.ECS_CONTAINER_NAME }}
          image: ${{ steps.build-image.outputs.image }}

      - name: Deploy ECS service
        uses: aws-actions/amazon-ecs-deploy-task-definition@v2
        with:
          task-definition: ${{ steps.render-task.outputs.task-definition }}
          service: ${{ env.ECS_SERVICE }}
          cluster: ${{ env.ECS_CLUSTER }}
          wait-for-service-stability: true
```

- [ ] **Step 3: Lint workflow YAML syntax**

Run:

```bash
python - <<'PY'
from pathlib import Path
import yaml
path = Path(".github/workflows/deploy-aws.yml")
with path.open() as f:
    yaml.safe_load(f)
print("workflow yaml ok")
PY
```

Expected:

```text
workflow yaml ok
```

- [ ] **Step 4: Commit workflow**

Run:

```bash
git add .github/workflows/deploy-aws.yml
git commit -m "ci: add ECS deployment workflow"
```

Expected: commit succeeds.

---

### Task 4: Provision AWS Network, DNS, TLS, And Registry

**Files:**
- Create directory: `docs/deploy`
- Create: `docs/deploy/aws-outputs.env`

- [ ] **Step 1: Set local shell constants**

Run:

```bash
export AWS_REGION=ap-east-2
export APP_NAME=open-domain-mcp
export SERVICE_NAME=open-domain-mcp-web
export DOMAIN_NAME=bwtseng.com
export APP_HOSTNAME=opendomain.bwtseng.com
export REPO_FULL_NAME=b5336789/open-domain-mcp
export VPC_CIDR=10.24.0.0/16
export SUBNET_A_CIDR=10.24.0.0/20
export SUBNET_B_CIDR=10.24.16.0/20
export CONTAINER_PORT=8000
export DB_IDENTIFIER=open-domain-mcp-db
export DB_NAME=opendomain_graph
export DB_USER=opendomain
mkdir -p docs/deploy
```

- [ ] **Step 2: Verify AWS identity and selected region**

Run:

```bash
aws sts get-caller-identity --region "$AWS_REGION"
aws ec2 describe-regions --region "$AWS_REGION" --region-names "$AWS_REGION"
```

Expected: caller identity JSON and one region entry for `ap-east-2`.

- [ ] **Step 3: Resolve two availability zones**

Run:

```bash
read AZ_A AZ_B < <(aws ec2 describe-availability-zones \
  --region "$AWS_REGION" \
  --filters Name=state,Values=available \
  --query 'AvailabilityZones[0:2].ZoneName' \
  --output text)
test -n "$AZ_A"
test -n "$AZ_B"
printf 'AZ_A=%s\nAZ_B=%s\n' "$AZ_A" "$AZ_B"
```

Expected: two availability zone names.

- [ ] **Step 4: Locate Route 53 hosted zone**

Run:

```bash
HOSTED_ZONE_ID=$(aws route53 list-hosted-zones-by-name \
  --dns-name "$DOMAIN_NAME." \
  --query "HostedZones[?Name=='$DOMAIN_NAME.'].Id | [0]" \
  --output text | sed 's#^/hostedzone/##')
test "$HOSTED_ZONE_ID" != "None"
test -n "$HOSTED_ZONE_ID"
echo "HOSTED_ZONE_ID=$HOSTED_ZONE_ID"
```

Expected: a Route 53 hosted zone ID.

- [ ] **Step 5: Request ACM certificate and validate through Route 53**

Run:

```bash
CERT_ARN=$(aws acm request-certificate \
  --region "$AWS_REGION" \
  --domain-name "$APP_HOSTNAME" \
  --validation-method DNS \
  --query CertificateArn \
  --output text)

sleep 10

VALIDATION_JSON=$(aws acm describe-certificate \
  --region "$AWS_REGION" \
  --certificate-arn "$CERT_ARN" \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord')

CERT_RECORD_NAME=$(printf '%s' "$VALIDATION_JSON" | jq -r '.Name')
CERT_RECORD_TYPE=$(printf '%s' "$VALIDATION_JSON" | jq -r '.Type')
CERT_RECORD_VALUE=$(printf '%s' "$VALIDATION_JSON" | jq -r '.Value')

jq -n \
  --arg name "$CERT_RECORD_NAME" \
  --arg type "$CERT_RECORD_TYPE" \
  --arg value "$CERT_RECORD_VALUE" \
  '{
    Changes: [{
      Action: "UPSERT",
      ResourceRecordSet: {
        Name: $name,
        Type: $type,
        TTL: 300,
        ResourceRecords: [{Value: $value}]
      }
    }]
  }' > /tmp/open-domain-mcp-acm-validation.json

aws route53 change-resource-record-sets \
  --hosted-zone-id "$HOSTED_ZONE_ID" \
  --change-batch file:///tmp/open-domain-mcp-acm-validation.json

aws acm wait certificate-validated \
  --region "$AWS_REGION" \
  --certificate-arn "$CERT_ARN"

echo "CERT_ARN=$CERT_ARN"
```

Expected: waiter exits successfully and certificate status is `ISSUED`.

- [ ] **Step 6: Create VPC, public subnets, and routes**

Run:

```bash
VPC_ID=$(aws ec2 create-vpc \
  --region "$AWS_REGION" \
  --cidr-block "$VPC_CIDR" \
  --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=$APP_NAME-vpc}]" \
  --query Vpc.VpcId \
  --output text)

aws ec2 modify-vpc-attribute --region "$AWS_REGION" --vpc-id "$VPC_ID" --enable-dns-hostnames '{"Value":true}'
aws ec2 modify-vpc-attribute --region "$AWS_REGION" --vpc-id "$VPC_ID" --enable-dns-support '{"Value":true}'

IGW_ID=$(aws ec2 create-internet-gateway \
  --region "$AWS_REGION" \
  --tag-specifications "ResourceType=internet-gateway,Tags=[{Key=Name,Value=$APP_NAME-igw}]" \
  --query InternetGateway.InternetGatewayId \
  --output text)

aws ec2 attach-internet-gateway --region "$AWS_REGION" --vpc-id "$VPC_ID" --internet-gateway-id "$IGW_ID"

SUBNET_A_ID=$(aws ec2 create-subnet \
  --region "$AWS_REGION" \
  --vpc-id "$VPC_ID" \
  --cidr-block "$SUBNET_A_CIDR" \
  --availability-zone "$AZ_A" \
  --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$APP_NAME-public-a}]" \
  --query Subnet.SubnetId \
  --output text)

SUBNET_B_ID=$(aws ec2 create-subnet \
  --region "$AWS_REGION" \
  --vpc-id "$VPC_ID" \
  --cidr-block "$SUBNET_B_CIDR" \
  --availability-zone "$AZ_B" \
  --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$APP_NAME-public-b}]" \
  --query Subnet.SubnetId \
  --output text)

aws ec2 modify-subnet-attribute --region "$AWS_REGION" --subnet-id "$SUBNET_A_ID" --map-public-ip-on-launch
aws ec2 modify-subnet-attribute --region "$AWS_REGION" --subnet-id "$SUBNET_B_ID" --map-public-ip-on-launch

ROUTE_TABLE_ID=$(aws ec2 create-route-table \
  --region "$AWS_REGION" \
  --vpc-id "$VPC_ID" \
  --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=$APP_NAME-public-rt}]" \
  --query RouteTable.RouteTableId \
  --output text)

aws ec2 create-route --region "$AWS_REGION" --route-table-id "$ROUTE_TABLE_ID" --destination-cidr-block 0.0.0.0/0 --gateway-id "$IGW_ID"
aws ec2 associate-route-table --region "$AWS_REGION" --route-table-id "$ROUTE_TABLE_ID" --subnet-id "$SUBNET_A_ID"
aws ec2 associate-route-table --region "$AWS_REGION" --route-table-id "$ROUTE_TABLE_ID" --subnet-id "$SUBNET_B_ID"
```

Expected: all commands exit successfully.

- [ ] **Step 7: Create security groups**

Run:

```bash
ALB_SG_ID=$(aws ec2 create-security-group \
  --region "$AWS_REGION" \
  --group-name "$APP_NAME-alb" \
  --description "$APP_NAME ALB" \
  --vpc-id "$VPC_ID" \
  --query GroupId \
  --output text)

ECS_SG_ID=$(aws ec2 create-security-group \
  --region "$AWS_REGION" \
  --group-name "$APP_NAME-ecs" \
  --description "$APP_NAME ECS tasks" \
  --vpc-id "$VPC_ID" \
  --query GroupId \
  --output text)

RDS_SG_ID=$(aws ec2 create-security-group \
  --region "$AWS_REGION" \
  --group-name "$APP_NAME-rds" \
  --description "$APP_NAME RDS MariaDB" \
  --vpc-id "$VPC_ID" \
  --query GroupId \
  --output text)

EFS_SG_ID=$(aws ec2 create-security-group \
  --region "$AWS_REGION" \
  --group-name "$APP_NAME-efs" \
  --description "$APP_NAME EFS" \
  --vpc-id "$VPC_ID" \
  --query GroupId \
  --output text)

aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$ALB_SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$ALB_SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$ECS_SG_ID" --protocol tcp --port "$CONTAINER_PORT" --source-group "$ALB_SG_ID"
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$RDS_SG_ID" --protocol tcp --port 3306 --source-group "$ECS_SG_ID"
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$EFS_SG_ID" --protocol tcp --port 2049 --source-group "$ECS_SG_ID"
```

Expected: all security groups and ingress rules are created.

- [ ] **Step 8: Create ECR repository**

Run:

```bash
aws ecr create-repository \
  --region "$AWS_REGION" \
  --repository-name "$APP_NAME" \
  --image-scanning-configuration scanOnPush=true

ECR_URI=$(aws ecr describe-repositories \
  --region "$AWS_REGION" \
  --repository-names "$APP_NAME" \
  --query 'repositories[0].repositoryUri' \
  --output text)

echo "ECR_URI=$ECR_URI"
```

Expected: ECR repository exists and `ECR_URI` is printed.

- [ ] **Step 9: Save non-secret outputs**

Run:

```bash
cat > docs/deploy/aws-outputs.env <<EOF
AWS_REGION=$AWS_REGION
APP_NAME=$APP_NAME
SERVICE_NAME=$SERVICE_NAME
DOMAIN_NAME=$DOMAIN_NAME
APP_HOSTNAME=$APP_HOSTNAME
REPO_FULL_NAME=$REPO_FULL_NAME
HOSTED_ZONE_ID=$HOSTED_ZONE_ID
CERT_ARN=$CERT_ARN
VPC_ID=$VPC_ID
IGW_ID=$IGW_ID
ROUTE_TABLE_ID=$ROUTE_TABLE_ID
SUBNET_A_ID=$SUBNET_A_ID
SUBNET_B_ID=$SUBNET_B_ID
ALB_SG_ID=$ALB_SG_ID
ECS_SG_ID=$ECS_SG_ID
RDS_SG_ID=$RDS_SG_ID
EFS_SG_ID=$EFS_SG_ID
ECR_URI=$ECR_URI
EOF
```

Expected: `docs/deploy/aws-outputs.env` contains no passwords, API keys, or database connection strings with credentials.

---

### Task 5: Provision AWS Data, IAM, Load Balancing, And ECS

**Files:**
- Modify: `docs/deploy/aws-outputs.env`
- Create: `deploy/aws/task-definition.json`

- [ ] **Step 1: Reload AWS outputs**

Run:

```bash
set -a
. docs/deploy/aws-outputs.env
set +a
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --region "$AWS_REGION" --query Account --output text)
```

Expected: variables from Task 4 are available in the shell.

- [ ] **Step 2: Create EFS file system, access point, and mount targets**

Run:

```bash
EFS_ID=$(aws efs create-file-system \
  --region "$AWS_REGION" \
  --encrypted \
  --tags Key=Name,Value="$APP_NAME-efs" \
  --query FileSystemId \
  --output text)

aws efs wait file-system-available --region "$AWS_REGION" --file-system-id "$EFS_ID"

EFS_ACCESS_POINT_ID=$(aws efs create-access-point \
  --region "$AWS_REGION" \
  --file-system-id "$EFS_ID" \
  --posix-user Uid=10001,Gid=10001 \
  --root-directory "Path=/opendomain,CreationInfo={OwnerUid=10001,OwnerGid=10001,Permissions=0755}" \
  --tags Key=Name,Value="$APP_NAME-access-point" \
  --query AccessPointId \
  --output text)

aws efs create-mount-target --region "$AWS_REGION" --file-system-id "$EFS_ID" --subnet-id "$SUBNET_A_ID" --security-groups "$EFS_SG_ID"
aws efs create-mount-target --region "$AWS_REGION" --file-system-id "$EFS_ID" --subnet-id "$SUBNET_B_ID" --security-groups "$EFS_SG_ID"
```

Expected: EFS file system and access point are created.

- [ ] **Step 3: Create RDS password secret and MariaDB instance**

Run:

```bash
RDS_PASSWORD=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 30)

RDS_SECRET_ARN=$(aws secretsmanager create-secret \
  --region "$AWS_REGION" \
  --name "$APP_NAME/rds/password" \
  --secret-string "$RDS_PASSWORD" \
  --query ARN \
  --output text)

aws rds create-db-subnet-group \
  --region "$AWS_REGION" \
  --db-subnet-group-name "$APP_NAME-db-subnets" \
  --db-subnet-group-description "$APP_NAME database subnets" \
  --subnet-ids "$SUBNET_A_ID" "$SUBNET_B_ID"

aws rds create-db-instance \
  --region "$AWS_REGION" \
  --db-instance-identifier "$DB_IDENTIFIER" \
  --db-instance-class db.t4g.micro \
  --engine mariadb \
  --allocated-storage 20 \
  --storage-type gp3 \
  --master-username "$DB_USER" \
  --master-user-password "$RDS_PASSWORD" \
  --db-name "$DB_NAME" \
  --vpc-security-group-ids "$RDS_SG_ID" \
  --db-subnet-group-name "$APP_NAME-db-subnets" \
  --backup-retention-period 7 \
  --deletion-protection \
  --no-publicly-accessible

aws rds wait db-instance-available \
  --region "$AWS_REGION" \
  --db-instance-identifier "$DB_IDENTIFIER"

RDS_ENDPOINT=$(aws rds describe-db-instances \
  --region "$AWS_REGION" \
  --db-instance-identifier "$DB_IDENTIFIER" \
  --query 'DBInstances[0].Endpoint.Address' \
  --output text)

unset RDS_PASSWORD
echo "RDS_ENDPOINT=$RDS_ENDPOINT"
```

Expected: RDS instance becomes available and `RDS_ENDPOINT` is printed.

- [ ] **Step 4: Create ECS cluster and CloudWatch log group**

Run:

```bash
aws ecs create-cluster --region "$AWS_REGION" --cluster-name "$APP_NAME"
aws logs create-log-group --region "$AWS_REGION" --log-group-name "/ecs/$SERVICE_NAME"
```

Expected: ECS cluster and log group exist.

- [ ] **Step 5: Create ECS task execution role and task role**

Run:

```bash
cat > /tmp/open-domain-mcp-ecs-task-trust.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON

EXECUTION_ROLE_ARN=$(aws iam create-role \
  --role-name "$APP_NAME-ecs-execution" \
  --assume-role-policy-document file:///tmp/open-domain-mcp-ecs-task-trust.json \
  --query Role.Arn \
  --output text)

TASK_ROLE_ARN=$(aws iam create-role \
  --role-name "$APP_NAME-ecs-task" \
  --assume-role-policy-document file:///tmp/open-domain-mcp-ecs-task-trust.json \
  --query Role.Arn \
  --output text)

aws iam attach-role-policy \
  --role-name "$APP_NAME-ecs-execution" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

jq -n \
  --arg secret "$RDS_SECRET_ARN" \
  '{
    Version: "2012-10-17",
    Statement: [{
      Effect: "Allow",
      Action: ["secretsmanager:GetSecretValue"],
      Resource: [$secret]
    }]
  }' > /tmp/open-domain-mcp-execution-secrets-policy.json

aws iam put-role-policy \
  --role-name "$APP_NAME-ecs-execution" \
  --policy-name "$APP_NAME-read-secrets" \
  --policy-document file:///tmp/open-domain-mcp-execution-secrets-policy.json

jq -n \
  --arg fs "arn:aws:elasticfilesystem:${AWS_REGION}:${AWS_ACCOUNT_ID}:file-system/${EFS_ID}" \
  '{
    Version: "2012-10-17",
    Statement: [{
      Effect: "Allow",
      Action: [
        "elasticfilesystem:ClientMount",
        "elasticfilesystem:ClientWrite"
      ],
      Resource: [$fs]
    }]
  }' > /tmp/open-domain-mcp-task-efs-policy.json

aws iam put-role-policy \
  --role-name "$APP_NAME-ecs-task" \
  --policy-name "$APP_NAME-efs-client" \
  --policy-document file:///tmp/open-domain-mcp-task-efs-policy.json
```

Expected: roles and inline policies are created.

- [ ] **Step 6: Create GitHub OIDC deploy role**

Run:

```bash
OIDC_PROVIDER_ARN=$(aws iam list-open-id-connect-providers \
  --query "OpenIDConnectProviderList[?ends_with(Arn, '/token.actions.githubusercontent.com')].Arn | [0]" \
  --output text)

if [ "$OIDC_PROVIDER_ARN" = "None" ] || [ -z "$OIDC_PROVIDER_ARN" ]; then
  OIDC_PROVIDER_ARN=$(aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 \
    --query OpenIDConnectProviderArn \
    --output text)
fi

jq -n \
  --arg provider "$OIDC_PROVIDER_ARN" \
  --arg repo "repo:$REPO_FULL_NAME:ref:refs/heads/main" \
  '{
    Version: "2012-10-17",
    Statement: [{
      Effect: "Allow",
      Principal: {Federated: $provider},
      Action: "sts:AssumeRoleWithWebIdentity",
      Condition: {
        StringEquals: {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        StringLike: {
          "token.actions.githubusercontent.com:sub": $repo
        }
      }
    }]
  }' > /tmp/open-domain-mcp-github-oidc-trust.json

GITHUB_ROLE_ARN=$(aws iam create-role \
  --role-name "$APP_NAME-github-deploy" \
  --assume-role-policy-document file:///tmp/open-domain-mcp-github-oidc-trust.json \
  --query Role.Arn \
  --output text)

jq -n \
  --arg region "$AWS_REGION" \
  --arg account "$AWS_ACCOUNT_ID" \
  --arg repo "arn:aws:ecr:${AWS_REGION}:${AWS_ACCOUNT_ID}:repository/${APP_NAME}" \
  --arg cluster "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:cluster/${APP_NAME}" \
  --arg service "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:service/${APP_NAME}/${SERVICE_NAME}" \
  --arg execution "$EXECUTION_ROLE_ARN" \
  --arg task "$TASK_ROLE_ARN" \
  '{
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Action: [
          "ecr:GetAuthorizationToken"
        ],
        Resource: "*"
      },
      {
        Effect: "Allow",
        Action: [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart"
        ],
        Resource: [$repo]
      },
      {
        Effect: "Allow",
        Action: [
          "ecs:DescribeServices",
          "ecs:DescribeTaskDefinition",
          "ecs:RegisterTaskDefinition",
          "ecs:UpdateService"
        ],
        Resource: "*"
      },
      {
        Effect: "Allow",
        Action: ["iam:PassRole"],
        Resource: [$execution, $task],
        Condition: {
          StringEquals: {
            "iam:PassedToService": "ecs-tasks.amazonaws.com"
          }
        }
      }
    ]
  }' > /tmp/open-domain-mcp-github-deploy-policy.json

aws iam put-role-policy \
  --role-name "$APP_NAME-github-deploy" \
  --policy-name "$APP_NAME-deploy" \
  --policy-document file:///tmp/open-domain-mcp-github-deploy-policy.json
```

Expected: GitHub deploy role exists and is restricted to `b5336789/open-domain-mcp` on `main`.

- [ ] **Step 7: Build and push initial image**

Run:

```bash
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

docker build -t "$ECR_URI:latest" .
docker push "$ECR_URI:latest"
```

Expected: image push succeeds.

- [ ] **Step 8: Create ALB and target group**

Run:

```bash
ALB_ARN=$(aws elbv2 create-load-balancer \
  --region "$AWS_REGION" \
  --name "$APP_NAME-alb" \
  --subnets "$SUBNET_A_ID" "$SUBNET_B_ID" \
  --security-groups "$ALB_SG_ID" \
  --type application \
  --scheme internet-facing \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text)

ALB_DNS_NAME=$(aws elbv2 describe-load-balancers \
  --region "$AWS_REGION" \
  --load-balancer-arns "$ALB_ARN" \
  --query 'LoadBalancers[0].DNSName' \
  --output text)

ALB_CANONICAL_ZONE_ID=$(aws elbv2 describe-load-balancers \
  --region "$AWS_REGION" \
  --load-balancer-arns "$ALB_ARN" \
  --query 'LoadBalancers[0].CanonicalHostedZoneId' \
  --output text)

TARGET_GROUP_ARN=$(aws elbv2 create-target-group \
  --region "$AWS_REGION" \
  --name "$SERVICE_NAME" \
  --protocol HTTP \
  --port "$CONTAINER_PORT" \
  --vpc-id "$VPC_ID" \
  --target-type ip \
  --health-check-enabled \
  --health-check-protocol HTTP \
  --health-check-path /api/health \
  --matcher HttpCode=200 \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text)

aws elbv2 create-listener \
  --region "$AWS_REGION" \
  --load-balancer-arn "$ALB_ARN" \
  --protocol HTTP \
  --port 80 \
  --default-actions Type=redirect,RedirectConfig='{Protocol=HTTPS,Port=443,StatusCode=HTTP_301}'

aws elbv2 create-listener \
  --region "$AWS_REGION" \
  --load-balancer-arn "$ALB_ARN" \
  --protocol HTTPS \
  --port 443 \
  --certificates CertificateArn="$CERT_ARN" \
  --default-actions Type=forward,TargetGroupArn="$TARGET_GROUP_ARN"
```

Expected: ALB, target group, and listeners exist.

- [ ] **Step 9: Create `deploy/aws/task-definition.json` with real AWS values**

Run:

```bash
mkdir -p deploy/aws

jq -n \
  --arg family "$SERVICE_NAME" \
  --arg executionRoleArn "$EXECUTION_ROLE_ARN" \
  --arg taskRoleArn "$TASK_ROLE_ARN" \
  --arg image "$ECR_URI:latest" \
  --arg region "$AWS_REGION" \
  --arg logGroup "/ecs/$SERVICE_NAME" \
  --arg containerName "$SERVICE_NAME" \
  --arg dataDir "/data/opendomain" \
  --arg rdsHost "$RDS_ENDPOINT" \
  --arg rdsPasswordSecret "$RDS_SECRET_ARN" \
  --arg efsId "$EFS_ID" \
  --arg efsAccessPoint "$EFS_ACCESS_POINT_ID" \
  --argjson port "$CONTAINER_PORT" \
  '{
    family: $family,
    taskRoleArn: $taskRoleArn,
    executionRoleArn: $executionRoleArn,
    networkMode: "awsvpc",
    requiresCompatibilities: ["FARGATE"],
    cpu: "1024",
    memory: "2048",
    runtimePlatform: {
      cpuArchitecture: "X86_64",
      operatingSystemFamily: "LINUX"
    },
    containerDefinitions: [{
      name: $containerName,
      image: $image,
      essential: true,
      portMappings: [{
        containerPort: $port,
        hostPort: $port,
        protocol: "tcp"
      }],
      environment: [
        {name: "ODM_WEB_HOST", value: "0.0.0.0"},
        {name: "ODM_WEB_PORT", value: ($port | tostring)},
        {name: "ODM_DATA_DIR", value: $dataDir},
        {name: "ODM_GRAPH_DB_HOST", value: $rdsHost},
        {name: "ODM_GRAPH_DB_PORT", value: "3306"},
        {name: "ODM_GRAPH_DB_USER", value: "opendomain"},
        {name: "ODM_GRAPH_DB_NAME", value: "opendomain_graph"}
      ],
      secrets: [
        {name: "ODM_GRAPH_DB_PASSWORD", valueFrom: $rdsPasswordSecret}
      ],
      mountPoints: [{
        sourceVolume: "opendomain-data",
        containerPath: $dataDir,
        readOnly: false
      }],
      logConfiguration: {
        logDriver: "awslogs",
        options: {
          "awslogs-group": $logGroup,
          "awslogs-region": $region,
          "awslogs-stream-prefix": "ecs"
        }
      }
    }],
    volumes: [{
      name: "opendomain-data",
      efsVolumeConfiguration: {
        fileSystemId: $efsId,
        transitEncryption: "ENABLED",
        authorizationConfig: {
          accessPointId: $efsAccessPoint,
          iam: "ENABLED"
        }
      }
    }]
  }' > deploy/aws/task-definition.json
```

Expected: `deploy/aws/task-definition.json` is valid JSON and contains no plaintext passwords.

- [ ] **Step 10: Register task definition and create ECS service**

Run:

```bash
TASK_DEFINITION_ARN=$(aws ecs register-task-definition \
  --region "$AWS_REGION" \
  --cli-input-json file://deploy/aws/task-definition.json \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)

aws ecs create-service \
  --region "$AWS_REGION" \
  --cluster "$APP_NAME" \
  --service-name "$SERVICE_NAME" \
  --task-definition "$TASK_DEFINITION_ARN" \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_A_ID,$SUBNET_B_ID],securityGroups=[$ECS_SG_ID],assignPublicIp=ENABLED}" \
  --load-balancers "targetGroupArn=$TARGET_GROUP_ARN,containerName=$SERVICE_NAME,containerPort=$CONTAINER_PORT" \
  --health-check-grace-period-seconds 120
```

Expected: ECS service is created.

- [ ] **Step 11: Create Route 53 application alias**

Run:

```bash
jq -n \
  --arg name "$APP_HOSTNAME" \
  --arg dns "$ALB_DNS_NAME" \
  --arg zone "$ALB_CANONICAL_ZONE_ID" \
  '{
    Changes: [{
      Action: "UPSERT",
      ResourceRecordSet: {
        Name: $name,
        Type: "A",
        AliasTarget: {
          HostedZoneId: $zone,
          DNSName: $dns,
          EvaluateTargetHealth: true
        }
      }
    }]
  }' > /tmp/open-domain-mcp-app-alias.json

aws route53 change-resource-record-sets \
  --hosted-zone-id "$HOSTED_ZONE_ID" \
  --change-batch file:///tmp/open-domain-mcp-app-alias.json
```

Expected: Route 53 alias is created.

- [ ] **Step 12: Append remaining non-secret outputs**

Run:

```bash
cat >> docs/deploy/aws-outputs.env <<EOF
AWS_ACCOUNT_ID=$AWS_ACCOUNT_ID
EFS_ID=$EFS_ID
EFS_ACCESS_POINT_ID=$EFS_ACCESS_POINT_ID
RDS_SECRET_ARN=$RDS_SECRET_ARN
RDS_ENDPOINT=$RDS_ENDPOINT
EXECUTION_ROLE_ARN=$EXECUTION_ROLE_ARN
TASK_ROLE_ARN=$TASK_ROLE_ARN
GITHUB_ROLE_ARN=$GITHUB_ROLE_ARN
ALB_ARN=$ALB_ARN
ALB_DNS_NAME=$ALB_DNS_NAME
ALB_CANONICAL_ZONE_ID=$ALB_CANONICAL_ZONE_ID
TARGET_GROUP_ARN=$TARGET_GROUP_ARN
TASK_DEFINITION_ARN=$TASK_DEFINITION_ARN
EOF
```

Expected: output file contains resource IDs, ARNs, DNS names, and no secrets.

---

### Task 6: Configure GitHub Repository Variables

**Files:**
- Read: `docs/deploy/aws-outputs.env`

- [ ] **Step 1: Reload outputs**

Run:

```bash
set -a
. docs/deploy/aws-outputs.env
set +a
```

Expected: `GITHUB_ROLE_ARN` is set.

- [ ] **Step 2: Set GitHub Actions variables**

Run:

```bash
gh variable set AWS_REGION --repo "$REPO_FULL_NAME" --body "$AWS_REGION"
gh variable set AWS_ROLE_ARN --repo "$REPO_FULL_NAME" --body "$GITHUB_ROLE_ARN"
gh variable set ECR_REPOSITORY --repo "$REPO_FULL_NAME" --body "$APP_NAME"
gh variable set ECS_CLUSTER --repo "$REPO_FULL_NAME" --body "$APP_NAME"
gh variable set ECS_SERVICE --repo "$REPO_FULL_NAME" --body "$SERVICE_NAME"
gh variable set ECS_TASK_DEFINITION --repo "$REPO_FULL_NAME" --body "deploy/aws/task-definition.json"
gh variable set ECS_CONTAINER_NAME --repo "$REPO_FULL_NAME" --body "$SERVICE_NAME"
```

Expected: all variables are saved in the GitHub repository.

- [ ] **Step 3: Verify GitHub Actions variables**

Run:

```bash
gh variable list --repo "$REPO_FULL_NAME"
```

Expected: the seven variables from Step 2 are listed.

---

### Task 7: Add AWS Deployment Runbook

**Files:**
- Create: `docs/deploy/aws-ecs-fargate.md`

- [ ] **Step 1: Create deployment runbook**

Use `apply_patch` to create `docs/deploy/aws-ecs-fargate.md` with this structure and the actual values from `docs/deploy/aws-outputs.env`:

````markdown
# AWS ECS Fargate Deployment

## Environment

- Region: ap-east-2
- Hostname: https://opendomain.bwtseng.com
- GitHub repository: b5336789/open-domain-mcp
- ECS cluster: open-domain-mcp
- ECS service: open-domain-mcp-web
- ECR repository: open-domain-mcp
- RDS DB identifier: open-domain-mcp-db
- RDS DB name: opendomain_graph
- EFS mount path in container: /data/opendomain

## Resource Outputs

See `docs/deploy/aws-outputs.env` for non-secret resource IDs and ARNs.

## Runtime

The ECS task runs:

```text
opendomainmcp-web
```

Required environment:

```text
ODM_WEB_HOST=0.0.0.0
ODM_WEB_PORT=8000
ODM_DATA_DIR=/data/opendomain
ODM_GRAPH_DB_HOST is the RDS_ENDPOINT value recorded in docs/deploy/aws-outputs.env.
ODM_GRAPH_DB_PORT=3306
ODM_GRAPH_DB_USER=opendomain
ODM_GRAPH_DB_NAME=opendomain_graph
```

`ODM_GRAPH_DB_PASSWORD` is injected from AWS Secrets Manager.

## GitHub Actions Variables

```text
AWS_REGION=ap-east-2
AWS_ROLE_ARN is the GITHUB_ROLE_ARN value recorded in docs/deploy/aws-outputs.env.
ECR_REPOSITORY=open-domain-mcp
ECS_CLUSTER=open-domain-mcp
ECS_SERVICE=open-domain-mcp-web
ECS_TASK_DEFINITION=deploy/aws/task-definition.json
ECS_CONTAINER_NAME=open-domain-mcp-web
```

## Deploy

Push to `main` or run the `Deploy to AWS ECS` workflow manually.

## Health Check

```bash
curl -fsS https://opendomain.bwtseng.com/api/health
```

Expected:

```json
{"status":"ok"}
```

## Rollback

List task definition revisions:

```bash
aws ecs list-task-definitions --region ap-east-2 --family-prefix open-domain-mcp-web --sort DESC
```

Redeploy a previous revision:

```bash
PREVIOUS_TASK_DEFINITION_ARN=$(aws ecs list-task-definitions --region ap-east-2 --family-prefix open-domain-mcp-web --sort DESC --query 'taskDefinitionArns[1]' --output text)
aws ecs update-service --region ap-east-2 --cluster open-domain-mcp --service open-domain-mcp-web --task-definition "$PREVIOUS_TASK_DEFINITION_ARN"
aws ecs wait services-stable --region ap-east-2 --cluster open-domain-mcp --services open-domain-mcp-web
```

## Teardown Order

1. Delete Route 53 alias for `opendomain.bwtseng.com`.
2. Delete ECS service after setting desired count to 0.
3. Delete ALB listeners, ALB, and target group.
4. Deregister task definitions that are no longer needed.
5. Delete ECS cluster.
6. Delete EFS mount targets, access point, and file system.
7. Disable RDS deletion protection only after choosing whether to keep a final snapshot.
8. Delete RDS instance.
9. Delete Secrets Manager secret after RDS is gone.
10. Delete ECR repository after preserving needed images.
11. Delete IAM roles and policies.
12. Delete security groups.
13. Detach and delete internet gateway.
14. Delete subnets, route table, and VPC.
15. Delete ACM certificate after DNS no longer points to the ALB.
````

- [ ] **Step 2: Validate docs and task definition**

Run:

```bash
jq empty deploy/aws/task-definition.json
rg -n "RDS_PASSWORD|ANTHROPIC_API_KEY|OPENAI_API_KEY|VOYAGE_API_KEY|SECRET_ACCESS_KEY" docs/deploy deploy/aws .github || true
```

Expected: `jq` succeeds. `rg` must not show secret values; references to secret variable names are acceptable only in docs or task definition secret mappings.

- [ ] **Step 3: Commit AWS deployment artifacts**

Run:

```bash
git add deploy/aws/task-definition.json docs/deploy/aws-outputs.env docs/deploy/aws-ecs-fargate.md
git commit -m "deploy: document AWS ECS Fargate environment"
```

Expected: commit succeeds.

---

### Task 8: Verify ECS, DNS, HTTPS, And GitHub Actions Deploy

**Files:**
- Read: `docs/deploy/aws-outputs.env`

- [ ] **Step 1: Wait for ECS service stability**

Run:

```bash
set -a
. docs/deploy/aws-outputs.env
set +a
aws ecs wait services-stable --region "$AWS_REGION" --cluster "$APP_NAME" --services "$SERVICE_NAME"
```

Expected: waiter exits successfully.

- [ ] **Step 2: Check ECS service events**

Run:

```bash
aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$APP_NAME" \
  --services "$SERVICE_NAME" \
  --query 'services[0].events[0:5].[createdAt,message]' \
  --output table
```

Expected: recent events include steady state or successful task start messages.

- [ ] **Step 3: Verify target group health**

Run:

```bash
aws elbv2 describe-target-health \
  --region "$AWS_REGION" \
  --target-group-arn "$TARGET_GROUP_ARN" \
  --query 'TargetHealthDescriptions[*].TargetHealth.State' \
  --output text
```

Expected:

```text
healthy
```

- [ ] **Step 4: Verify HTTPS health route**

Run:

```bash
curl -fsS "https://$APP_HOSTNAME/api/health"
```

Expected:

```json
{"status":"ok"}
```

- [ ] **Step 5: Trigger GitHub Actions deploy**

Run:

```bash
gh workflow run "Deploy to AWS ECS" --repo "$REPO_FULL_NAME" --ref main
sleep 10
gh run list --repo "$REPO_FULL_NAME" --workflow "Deploy to AWS ECS" --limit 1
```

Expected: a new workflow run appears.

- [ ] **Step 6: Watch GitHub Actions deploy**

Run:

```bash
RUN_ID=$(gh run list --repo "$REPO_FULL_NAME" --workflow "Deploy to AWS ECS" --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN_ID" --repo "$REPO_FULL_NAME" --exit-status
```

Expected: workflow completes successfully.

- [ ] **Step 7: Re-verify service after GitHub deploy**

Run:

```bash
aws ecs wait services-stable --region "$AWS_REGION" --cluster "$APP_NAME" --services "$SERVICE_NAME"
curl -fsS "https://$APP_HOSTNAME/api/health"
```

Expected:

```json
{"status":"ok"}
```

---

### Task 9: Final Review

**Files:**
- Read: `Dockerfile`
- Read: `.dockerignore`
- Read: `.github/workflows/deploy-aws.yml`
- Read: `deploy/aws/task-definition.json`
- Read: `docs/deploy/aws-ecs-fargate.md`
- Read: `docs/deploy/aws-outputs.env`

- [ ] **Step 1: Confirm no secrets are committed**

Run:

```bash
git grep -nE '(RDS_PASSWORD=|ANTHROPIC_API_KEY=sk-|OPENAI_API_KEY=sk-|VOYAGE_API_KEY=|AWS_SECRET_ACCESS_KEY=|mysql://.*:.*@)'
```

Expected: no output.

- [ ] **Step 2: Confirm working tree is clean**

Run:

```bash
git status --short
```

Expected: no output.

- [ ] **Step 3: Confirm final commits**

Run:

```bash
git log --oneline -8
```

Expected: recent commits include Docker packaging, GitHub Actions workflow, and AWS deployment artifacts.

- [ ] **Step 4: Summarize deployment state**

Report:

- ECS service URL: `https://opendomain.bwtseng.com`
- Health route result from Task 8.
- Latest GitHub Actions run status.
- RDS deletion protection status.
- EFS file system ID.
- Rollback command location: `docs/deploy/aws-ecs-fargate.md`.

---

## Self-Review Notes

- Spec coverage: repository artifacts, AWS provisioning, OIDC, EFS, RDS, ALB HTTPS, Route 53, verification, and rollback are covered.
- Secrets handling: RDS password is generated locally, stored only in Secrets Manager, unset from shell after RDS creation, and not written to repo files.
- Task definition: produced after AWS provisioning so it can contain real ARNs and IDs rather than inert values.
- Cost posture: no NAT Gateway is created; ECS tasks use public subnets and public IPs while app ingress is limited to ALB security group traffic.
- Known operational delay: ACM validation, RDS creation, and ECS service stabilization can each take several minutes.

# AWS ECS Fargate Deployment

## Environment

- Region: ap-east-2
- Hostname: https://opendomain.bwtseng.com
- GitHub repository: b5336789/open-domain-mcp
- ECS cluster: open-domain-mcp
- ECS service: open-domain-mcp-web
- ECR repository: open-domain-mcp
- RDS DB identifier: open-domain-mcp-db-enc
- RDS DB name: opendomain_graph
- RDS storage encryption: enabled
- EFS file system: fs-047117cca0ea5c82b
- EFS access point: fsap-0e58045b3846e8717
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
ODM_GRAPH_DB_HOST=open-domain-mcp-db-enc.c9ok44ak0wmz.ap-east-2.rds.amazonaws.com
ODM_GRAPH_DB_PORT=3306
ODM_GRAPH_DB_USER=opendomain
ODM_GRAPH_DB_NAME=opendomain_graph
```

`ODM_GRAPH_DB_PASSWORD` is injected from AWS Secrets Manager.

## GitHub Actions Variables

```text
AWS_REGION=ap-east-2
AWS_ROLE_ARN=arn:aws:iam::334317074103:role/open-domain-mcp-github-deploy
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

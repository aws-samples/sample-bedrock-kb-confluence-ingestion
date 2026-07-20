#!/usr/bin/env bash
# Post-deploy script: creates the AOSS vector index by running the
# ckn-create-aoss-index Fargate task on a private subnet.
#
# Usage:
#   ./scripts/create-aoss-index.sh [--profile PROFILE] [--region REGION]
#
# Defaults:
#   --profile default
#   --region  us-east-1

set -euo pipefail

PROFILE="default"
REGION="us-east-1"
CLUSTER="ckn-ingestion"
TASK_FAMILY="ckn-create-aoss-index"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --region)  REGION="$2";  shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "==> Finding private subnet (CDK-tagged)..."
SUBNET_ID=$(aws ec2 describe-subnets \
  --filters "Name=tag:aws-cdk:subnet-type,Values=Private" \
            "Name=tag:aws:cloudformation:stack-name,Values=CknIngestionStack" \
  --query "Subnets[0].SubnetId" --output text \
  --region "$REGION" --profile "$PROFILE")

if [[ -z "$SUBNET_ID" || "$SUBNET_ID" == "None" ]]; then
  echo "ERROR: No private subnet found. Check CDK stack tags." >&2
  exit 1
fi
echo "    Subnet: $SUBNET_ID"

echo "==> Running index-creator Fargate task..."
TASK_ARN=$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_FAMILY" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_ID],assignPublicIp=DISABLED}" \
  --region "$REGION" --profile "$PROFILE" \
  --query "tasks[0].taskArn" --output text)

if [[ -z "$TASK_ARN" || "$TASK_ARN" == "None" ]]; then
  echo "ERROR: Failed to start Fargate task." >&2
  exit 1
fi
echo "    Task: $TASK_ARN"

echo "==> Waiting for task to complete..."
aws ecs wait tasks-stopped \
  --cluster "$CLUSTER" \
  --tasks "$TASK_ARN" \
  --region "$REGION" --profile "$PROFILE"

EXIT_CODE=$(aws ecs describe-tasks \
  --cluster "$CLUSTER" \
  --tasks "$TASK_ARN" \
  --region "$REGION" --profile "$PROFILE" \
  --query "tasks[0].containers[0].exitCode" --output text)

if [[ "$EXIT_CODE" == "0" ]]; then
  echo "==> Index created successfully (or already existed)."
else
  echo "ERROR: Index creator task failed with exit code $EXIT_CODE" >&2
  echo "    Check logs: aws logs tail /ckn/ingestion --filter-pattern create-index --profile $PROFILE --region $REGION"
  exit 1
fi

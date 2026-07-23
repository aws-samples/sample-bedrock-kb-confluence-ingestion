#!/usr/bin/env bash
# ============================================================================
# CKN Ingestion Pipeline — Teardown Script
#
# Destroys all resources created by the deploy script, including RETAIN'd
# resources and the CDK bootstrap toolkit stack.
#
# Usage:
#   ./scripts/teardown.sh --account 123456789012 --region us-west-2
#
# CAUTION: This permanently deletes all data (S3, KMS, logs, ECR images).
# ============================================================================
set -euo pipefail

ACCOUNT=""
REGION=""
PROFILE="${AWS_PROFILE:-default}"
FORCE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account) ACCOUNT="$2"; shift 2 ;;
    --region)  REGION="$2";  shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --force)   FORCE=1;      shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$ACCOUNT" || -z "$REGION" ]]; then
  echo "Usage: $0 --account ACCOUNT_ID --region REGION [--profile PROFILE] [--force]"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

aws_cmd() {
  aws --profile "$PROFILE" --region "$REGION" "$@"
}

echo "============================================================"
echo "CKN Ingestion Pipeline — TEARDOWN"
echo "  Account:  $ACCOUNT"
echo "  Region:   $REGION"
echo "  Profile:  $PROFILE"
echo ""
echo "  This will PERMANENTLY DELETE:"
echo "    - CDK stack (CknIngestionStack)"
echo "    - S3 buckets (ams-ckn-$ACCOUNT, ams-ckn-$ACCOUNT-access-logs)"
echo "    - KMS key (scheduled for deletion)"
echo "    - ECR repository (ckn-ingestion)"
echo "    - CloudWatch log group (/ckn/ingestion)"
echo "    - Secrets Manager secret (ams/ckn/confluence-token)"
echo "    - CDK Toolkit stack (CDKToolkit-cknpipe)"
echo "============================================================"

if [[ -z "$FORCE" ]]; then
  echo ""
  read -rp "Are you sure? Type 'yes' to proceed: " CONFIRM
  if [[ "$CONFIRM" != "yes" ]]; then
    echo "Aborted."
    exit 0
  fi
fi

echo ""
echo "==> [1/8] Disabling EventBridge schedule..."
aws_cmd events disable-rule --name ckn-ingestion-daily 2>/dev/null || true
echo "    Done."

echo ""
echo "==> [2/8] Destroying CDK stack..."
cd "$REPO_ROOT/src/infra"
npx cdk destroy --force --toolkit-stack-name CDKToolkit-cknpipe 2>&1 || true
cd "$REPO_ROOT"
echo "    Done (RETAIN'd resources still exist)."

echo ""
echo "==> [3/8] Emptying and deleting S3 buckets..."
for BUCKET in "ams-ckn-$ACCOUNT" "ams-ckn-$ACCOUNT-access-logs"; do
  if aws_cmd s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "    Emptying $BUCKET..."
    aws_cmd s3 rm "s3://$BUCKET" --recursive 2>/dev/null || true
    echo "    Deleting $BUCKET..."
    aws_cmd s3api delete-bucket --bucket "$BUCKET" 2>/dev/null || true
  else
    echo "    $BUCKET does not exist (skipped)."
  fi
done

echo ""
echo "==> [4/8] Deleting ECR repository..."
aws_cmd ecr delete-repository --repository-name ckn-ingestion --force 2>/dev/null || true
echo "    Done."

echo ""
echo "==> [5/8] Deleting CloudWatch log groups..."
aws_cmd logs delete-log-group --log-group-name /ckn/ingestion 2>/dev/null || true
aws_cmd logs delete-log-group --log-group-name /aws/cloudtrail/ckn-trail 2>/dev/null || true
echo "    Done."

echo ""
echo "==> [6/8] Deleting Secrets Manager secret..."
aws_cmd secretsmanager delete-secret --secret-id ams/ckn/confluence-token \
  --force-delete-without-recovery 2>/dev/null || true
echo "    Done."

echo ""
echo "==> [7/8] Scheduling KMS key deletion (30-day waiting period)..."
# Look up the key ARN from the CDK stack's output (most reliable identifier)
KEY_ARN=$(aws_cmd cloudformation describe-stacks --stack-name CknIngestionStack \
  --query 'Stacks[0].Outputs[?OutputKey==`KmsKeyArn`].OutputValue' --output text 2>/dev/null || echo "")
if [[ -n "$KEY_ARN" && "$KEY_ARN" != "None" ]]; then
  aws_cmd kms schedule-key-deletion --key-id "$KEY_ARN" --pending-window-in-days 30 2>/dev/null || true
  echo "    Key $KEY_ARN scheduled for deletion in 30 days."
else
  echo "    Could not find KMS key from stack outputs (stack may already be deleted)."
  echo "    If the key still exists, schedule deletion manually:"
  echo "      aws kms schedule-key-deletion --key-id KEY_ID --pending-window-in-days 30"
fi

echo ""
echo "==> [8/8] Deleting CDK Toolkit stack..."
# Empty the CDK staging bucket first
STAGING_BUCKET=$(aws_cmd cloudformation describe-stack-resources \
  --stack-name CDKToolkit-cknpipe \
  --query "StackResources[?ResourceType=='AWS::S3::Bucket'].PhysicalResourceId" \
  --output text 2>/dev/null || echo "")
if [[ -n "$STAGING_BUCKET" ]]; then
  aws_cmd s3 rm "s3://$STAGING_BUCKET" --recursive 2>/dev/null || true
fi
aws_cmd cloudformation delete-stack --stack-name CDKToolkit-cknpipe 2>/dev/null || true
echo "    Done."

echo ""
echo "============================================================"
echo "Teardown complete."
echo ""
echo "Resources that may take time to fully delete:"
echo "  - KMS key: 30-day waiting period (cancel with: aws kms cancel-key-deletion)"
echo "  - CloudFormation stacks: may take a few minutes"
echo "============================================================"

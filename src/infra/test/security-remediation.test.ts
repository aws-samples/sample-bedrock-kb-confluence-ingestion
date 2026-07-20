import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { CknIngestionStack } from '../lib/ckn-ingestion-stack';

/**
 * Security Remediation CDK Assertion Tests
 *
 * Validates infrastructure changes from the security review remediation spec:
 * - S3 bucket uses KMS encryption with a customer-managed key (Requirement 1.1)
 * - ECR repository has image scanning on push enabled (Requirement 8.1)
 * - Fargate task definition has 16 GB memory and 4 vCPU (Requirements 11.1, 11.2)
 */

let template: Template;

beforeAll(() => {
  const app = new cdk.App();
  const stack = new CknIngestionStack(app, 'SecurityTestStack', {
    env: { account: '123456789012', region: 'us-east-1' },
  });
  template = Template.fromStack(stack);
});

// Validates: Requirement 1.1
describe('S3 bucket KMS encryption', () => {
  it('uses aws:kms server-side encryption with a KMS key reference', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      BucketEncryption: {
        ServerSideEncryptionConfiguration: Match.arrayWith([
          Match.objectLike({
            ServerSideEncryptionByDefault: {
              SSEAlgorithm: 'aws:kms',
              KMSMasterKeyID: Match.anyValue(),
            },
          }),
        ]),
      },
    });
  });

  it('KMS key reference points to a KMS key resource in the stack', () => {
    // Find the S3 bucket that uses aws:kms encryption
    const buckets = template.findResources('AWS::S3::Bucket');
    const kmsBucket = Object.values(buckets).find((b: any) => {
      const sse = b.Properties?.BucketEncryption
        ?.ServerSideEncryptionConfiguration?.[0]
        ?.ServerSideEncryptionByDefault;
      return sse?.SSEAlgorithm === 'aws:kms';
    }) as any;
    expect(kmsBucket).toBeDefined();

    const keyRef = kmsBucket.Properties.BucketEncryption
      .ServerSideEncryptionConfiguration[0]
      .ServerSideEncryptionByDefault.KMSMasterKeyID;
    expect(keyRef).toBeDefined();

    // Verify a KMS key resource exists in the template
    template.resourceCountIs('AWS::KMS::Key', 1);
  });
});

// Validates: Requirement 8.1
describe('ECR repository image scanning', () => {
  it('has ScanOnPush enabled', () => {
    template.hasResourceProperties('AWS::ECR::Repository', {
      ImageScanningConfiguration: {
        ScanOnPush: true,
      },
    });
  });
});

// Validates: Requirements 11.1, 11.2
describe('Fargate task definition sizing', () => {
  it('ckn-ingestion task has 16384 MB memory and 4096 CPU', () => {
    template.hasResourceProperties('AWS::ECS::TaskDefinition', {
      Family: 'ckn-ingestion',
      Memory: '16384',
      Cpu: '4096',
    });
  });
});

// Validates: BSC43.1 — S3 server access logging
describe('S3 server access logging', () => {
  it('main bucket has LoggingConfiguration with a DestinationBucketName reference', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      BucketName: Match.stringLikeRegexp('^ams-ckn-.*(?<!-access-logs)$'),
      LoggingConfiguration: {
        DestinationBucketName: Match.anyValue(),
        LogFilePrefix: 'access-logs/',
      },
    });
  });
});

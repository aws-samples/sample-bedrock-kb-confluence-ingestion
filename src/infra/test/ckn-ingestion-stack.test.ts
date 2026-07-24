import * as cdk from 'aws-cdk-lib';
import * as fc from 'fast-check';
import { CknIngestionStack } from '../lib/ckn-ingestion-stack';

/**
 * Helper: synthesize the stack with a given account ID and return the CloudFormation template.
 *
 * The VPC is now CDK-managed (new ec2.Vpc), so no VPC lookup context is needed.
 */
function synthesizeTemplate(
  accountId: string,
  opts: { deployKb?: boolean } = {},
): Record<string, any> {
  // The Bedrock KB + S3 data source are gated behind the `deployKb` context
  // (two-phase deploy). Tests that assert on KB/DataSource resources must
  // synthesize with it enabled, or those resources are absent from the template.
  const app = new cdk.App({
    context: opts.deployKb ? { deployKb: 'true' } : {},
  });

  const stack = new CknIngestionStack(app, 'TestStack', {
    env: { account: accountId, region: 'us-east-1' },
  });

  return app.synth().getStackArtifact(stack.artifactId).template;
}

/**
 * Generator for valid 12-digit AWS account IDs (numeric strings, no leading zero).
 */
const awsAccountId = fc
  .array(fc.constantFrom('0','1','2','3','4','5','6','7','8','9'), { minLength: 12, maxLength: 12 })
  .map(chars => chars.join(''))
  .filter(s => s[0] !== '0');

/**
 * Helper: extract raw Principal entries from the AOSS data access policy.
 *
 * The CfnAccessPolicy `Policy` property is built with JSON.stringify containing
 * CDK token references. CDK synthesizes this as an Fn::Join intrinsic where
 * role ARN tokens become Fn::GetAtt references. We extract the raw parts
 * (strings and intrinsics) from the Fn::Join so tests can inspect them.
 */
function extractDataAccessPolicyRaw(template: Record<string, any>): { policyProp: any; resources: Record<string, any> } {
  const resources = template.Resources ?? {};
  for (const [, resource] of Object.entries(resources) as [string, any][]) {
    if (resource.Type !== 'AWS::OpenSearchServerless::AccessPolicy') continue;
    if (resource.Properties?.Type !== 'data') continue;
    return { policyProp: resource.Properties.Policy, resources };
  }
  throw new Error('No AOSS data access policy found in template');
}

/**
 * From the raw Fn::Join policy value, extract the logical IDs of all IAM roles
 * referenced as Principals via Fn::GetAtt, and any literal string principals.
 */
function extractPrincipalRefs(policyProp: any): { roleLogicalIds: string[]; literalPrincipals: string[] } {
  const roleLogicalIds: string[] = [];
  const literalPrincipals: string[] = [];

  if (typeof policyProp === 'string') {
    // Fully resolved — parse and extract literal principals
    const doc = JSON.parse(policyProp);
    for (const ruleSet of doc) {
      const prins = Array.isArray(ruleSet.Principal) ? ruleSet.Principal : [ruleSet.Principal];
      literalPrincipals.push(...prins.filter((p: any) => typeof p === 'string'));
    }
    return { roleLogicalIds, literalPrincipals };
  }

  // Fn::Join case — walk the parts array looking for Fn::GetAtt entries
  if (policyProp && typeof policyProp === 'object' && 'Fn::Join' in policyProp) {
    const parts: any[] = policyProp['Fn::Join'][1];
    // Concatenate all string parts to find any literal ARNs
    let fullString = '';
    for (const part of parts) {
      if (typeof part === 'string') {
        fullString += part;
      } else if (part && typeof part === 'object') {
        if ('Fn::GetAtt' in part) {
          const [logicalId, attr] = part['Fn::GetAtt'];
          if (attr === 'Arn') {
            roleLogicalIds.push(logicalId);
          }
          fullString += `__REF_${logicalId}__`;
        }
      }
    }
    // Check for any literal IAM ARNs in the string parts
    const arnPattern = /arn:aws:iam::\d+:(user|role)\/[^\s"\\,\]]+/g;
    let match;
    while ((match = arnPattern.exec(fullString)) !== null) {
      literalPrincipals.push(match[0]);
    }
  }

  return { roleLogicalIds, literalPrincipals };
}

// Feature: automate-deployment-tasks, Property 1: Data access policy contains only CDK-managed roles
// **Validates: Requirements 1.1, 1.2**
describe('Property 1: Data access policy contains only CDK-managed roles', () => {
  it('Principal array contains only role ARNs for ckn-bedrock-kb-role and ckn-ingestion-task-role, and no IAM user ARNs', () => {
    fc.assert(
      fc.property(awsAccountId, (accountId) => {
        const template = synthesizeTemplate(accountId);
        const { policyProp, resources } = extractDataAccessPolicyRaw(template);
        const { roleLogicalIds, literalPrincipals } = extractPrincipalRefs(policyProp);

        // No literal IAM user ARNs should appear
        for (const lit of literalPrincipals) {
          expect(lit).not.toMatch(/:user\//);
        }

        // All principal references should be Fn::GetAtt to IAM roles
        expect(roleLogicalIds.length).toBe(2);

        // Each referenced logical ID must be an IAM Role with the expected role name
        const allowedRoleNames = ['ckn-bedrock-kb-role', 'ckn-ingestion-task-role'];
        const foundRoleNames: string[] = [];
        for (const logicalId of roleLogicalIds) {
          const res = resources[logicalId];
          expect(res).toBeDefined();
          expect(res.Type).toBe('AWS::IAM::Role');
          const roleName = res.Properties?.RoleName;
          expect(allowedRoleNames).toContain(roleName);
          foundRoleNames.push(roleName);
        }

        // Both roles must be present
        expect(foundRoleNames.sort()).toEqual(allowedRoleNames.sort());
      }),
      { numRuns: 100 }
    );
  });
});

// Feature: automate-deployment-tasks, Property 2: Principal ARNs resolve to the deploying account
// **Validates: Requirements 1.3**
describe('Property 2: Principal ARNs resolve to the deploying account', () => {
  it('all Principal ARNs in the data access policy contain the generated account ID', () => {
    fc.assert(
      fc.property(awsAccountId, (accountId) => {
        const template = synthesizeTemplate(accountId);
        const { policyProp, resources } = extractDataAccessPolicyRaw(template);
        const { roleLogicalIds, literalPrincipals } = extractPrincipalRefs(policyProp);

        // Any literal ARNs must contain the deploying account ID
        for (const lit of literalPrincipals) {
          expect(lit).toContain(accountId);
        }

        // For Fn::GetAtt references: the IAM roles use the stack's account.
        // Verify the role ARN will resolve to the correct account by checking
        // that the role's assume-role policy or the stack itself uses the account.
        // Since CDK IAM roles are stack-scoped, their ARN is always
        // arn:aws:iam::{stack-account}:role/{roleName}.
        // We verify this by confirming the roles exist in the same template
        // (same stack = same account) and that no cross-account references exist.
        expect(roleLogicalIds.length).toBeGreaterThan(0);
        for (const logicalId of roleLogicalIds) {
          const res = resources[logicalId];
          expect(res).toBeDefined();
          expect(res.Type).toBe('AWS::IAM::Role');

          // The role's Arn intrinsic (Fn::GetAtt) resolves to
          // arn:aws:iam::{AWS::AccountId}:role/{RoleName} at deploy time.
          // Since the role is in the same stack, AWS::AccountId == the deploying account.
          // Verify the role is not imported from another account by checking
          // it has Properties (i.e., it's a real resource, not a reference).
          expect(res.Properties).toBeDefined();
        }

        // Additionally verify the template's account matches by checking
        // that no hardcoded account IDs appear in the policy string parts
        // that differ from the deploying account.
        if (typeof policyProp === 'object' && 'Fn::Join' in policyProp) {
          const parts: any[] = policyProp['Fn::Join'][1];
          const stringParts = parts.filter((p: any) => typeof p === 'string').join('');
          const accountPattern = /\d{12}/g;
          let match;
          while ((match = accountPattern.exec(stringParts)) !== null) {
            // Any 12-digit number in the policy string should be the deploying account
            expect(match[0]).toBe(accountId);
          }
        }
      }),
      { numRuns: 100 }
    );
  });
});

// Feature: automate-deployment-tasks, Property 3: Task role IAM policies use scoped resources
// **Validates: Requirements 3.5, 3.6**
describe('Property 3: Task role IAM policies use scoped resources', () => {
  it('kms:Decrypt and secretsmanager:GetSecretValue never use "*" as the Resource', () => {
    fc.assert(
      fc.property(awsAccountId, (accountId) => {
        const template = synthesizeTemplate(accountId);
        const resources = template.Resources ?? {};

        for (const [logicalId, resource] of Object.entries(resources) as [string, any][]) {
          // Find IAM roles with inline policies
          if (resource.Type !== 'AWS::IAM::Role') continue;

          const policies = resource.Properties?.Policies ?? [];
          for (const policy of policies) {
            const statements = policy.PolicyDocument?.Statement ?? [];
            for (const stmt of statements) {
              const actions: string[] = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
              const hasKmsDecrypt = actions.some((a: string) => a === 'kms:Decrypt');
              const hasSecretsManagerGet = actions.some((a: string) => a === 'secretsmanager:GetSecretValue');

              if (hasKmsDecrypt || hasSecretsManagerGet) {
                // Resource must not be "*"
                const resourceField = stmt.Resource;
                if (Array.isArray(resourceField)) {
                  for (const r of resourceField) {
                    expect(r).not.toBe('*');
                  }
                } else {
                  expect(resourceField).not.toBe('*');
                }
              }
            }
          }
        }
      }),
      { numRuns: 100 }
    );
  });
});

// ---------------------------------------------------------------------------
// Task 4.3: Unit tests for Index Creator custom resource
// **Validates: Requirements 2.1, 2.8, 2.10**
// ---------------------------------------------------------------------------

/**
 * Helper: find all resources of a given type in the template.
 */
function findResources(template: Record<string, any>, type: string): [string, any][] {
  const resources = template.Resources ?? {};
  return (Object.entries(resources) as [string, any][]).filter(
    ([, res]) => res.Type === type
  );
}

describe('Index Creator Fargate task definition', () => {
  // Synthesize once for the deterministic unit tests
  const template = synthesizeTemplate('123456789012');
  const resources = template.Resources ?? {};

  it('exists with CPU 256 and memory 512', () => {
    const taskDefs = findResources(template, 'AWS::ECS::TaskDefinition');
    const indexCreatorTd = taskDefs.find(([, res]) =>
      res.Properties?.Family === 'ckn-create-aoss-index'
    );

    expect(indexCreatorTd).toBeDefined();
    const props = indexCreatorTd![1].Properties;
    expect(props.Cpu).toBe('256');
    expect(props.Memory).toBe('512');
  });

  it('uses the existing taskRole and executionRole', () => {
    const taskDefs = findResources(template, 'AWS::ECS::TaskDefinition');
    const indexCreatorTd = taskDefs.find(([, res]) =>
      res.Properties?.Family === 'ckn-create-aoss-index'
    );

    expect(indexCreatorTd).toBeDefined();
    const props = indexCreatorTd![1].Properties;

    // TaskRoleArn should reference the ckn-ingestion-task-role
    const taskRoleRef = props.TaskRoleArn;
    expect(taskRoleRef).toBeDefined();
    const taskRoleLogicalId = taskRoleRef?.['Fn::GetAtt']?.[0];
    if (taskRoleLogicalId) {
      const role = resources[taskRoleLogicalId];
      expect(role?.Properties?.RoleName).toBe('ckn-ingestion-task-role');
    }

    // ExecutionRoleArn should reference the ckn-ingestion-execution-role
    const execRoleRef = props.ExecutionRoleArn;
    expect(execRoleRef).toBeDefined();
    const execRoleLogicalId = execRoleRef?.['Fn::GetAtt']?.[0];
    if (execRoleLogicalId) {
      const role = resources[execRoleLogicalId];
      expect(role?.Properties?.RoleName).toBe('ckn-ingestion-execution-role');
    }
  });
});


// ---------------------------------------------------------------------------
// Task 5.3: Unit tests for Bedrock KB configuration
// **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.7, 4.8**
// ---------------------------------------------------------------------------

describe('Bedrock Knowledge Base configuration', () => {
  const template = synthesizeTemplate('123456789012', { deployKb: true });

  describe('CfnKnowledgeBase', () => {
    it('exists with correct name and embedding model', () => {
      const kbs = findResources(template, 'AWS::Bedrock::KnowledgeBase');
      expect(kbs.length).toBe(1);

      const [, kbResource] = kbs[0];
      const props = kbResource.Properties;

      expect(props.Name).toBe('ckn-knowledge-base');

      // Verify VECTOR type KB configuration
      const kbConfig = props.KnowledgeBaseConfiguration;
      expect(kbConfig.Type).toBe('VECTOR');

      // Embedding model ARN should reference titan-embed-text-v2:0
      const embeddingArn = kbConfig.VectorKnowledgeBaseConfiguration.EmbeddingModelArn;
      expect(embeddingArn).toContain('amazon.titan-embed-text-v2:0');
    });

    it('uses AOSS storage config with correct field mappings', () => {
      const kbs = findResources(template, 'AWS::Bedrock::KnowledgeBase');
      expect(kbs.length).toBe(1);

      const storageConfig = kbs[0][1].Properties.StorageConfiguration;
      expect(storageConfig.Type).toBe('OPENSEARCH_SERVERLESS');

      const aossConfig = storageConfig.OpensearchServerlessConfiguration;
      expect(aossConfig.VectorIndexName).toBe('bedrock-knowledge-base-default-index');

      const fieldMapping = aossConfig.FieldMapping;
      expect(fieldMapping.VectorField).toBe('bedrock-knowledge-base-default-vector');
      expect(fieldMapping.TextField).toBe('AMAZON_BEDROCK_TEXT_CHUNK');
      expect(fieldMapping.MetadataField).toBe('AMAZON_BEDROCK_METADATA');
    });

    it('exists in the template', () => {
      const kbs = findResources(template, 'AWS::Bedrock::KnowledgeBase');
      expect(kbs.length).toBe(1);
    });
  });

  describe('CfnDataSource', () => {
    it('exists with correct S3 config and inclusion prefix', () => {
      const dataSources = findResources(template, 'AWS::Bedrock::DataSource');
      expect(dataSources.length).toBe(1);

      const [, dsResource] = dataSources[0];
      const props = dsResource.Properties;

      expect(props.Name).toBe('ckn-confluence-s3');

      const dsConfig = props.DataSourceConfiguration;
      expect(dsConfig.Type).toBe('S3');
      expect(dsConfig.S3Configuration.InclusionPrefixes).toEqual(['confluence/']);
    });

    it('disables KB re-chunking (NONE) so the pipeline owns chunking (F9 Option A)', () => {
      const dataSources = findResources(template, 'AWS::Bedrock::DataSource');
      expect(dataSources.length).toBe(1);

      const chunkingConfig = dataSources[0][1].Properties
        .VectorIngestionConfiguration.ChunkingConfiguration;

      // The pipeline (content_splitter.split_markdown) pre-chunks and size-caps
      // each page, so Bedrock must embed each S3 object as-is (one object ->
      // one vector) rather than re-chunking it.
      expect(chunkingConfig.ChunkingStrategy).toBe('NONE');

      // No semantic-chunking sub-config should remain when strategy is NONE.
      expect(chunkingConfig.SemanticChunkingConfiguration).toBeUndefined();
    });
  });

  describe('CDK outputs', () => {
    it('includes KnowledgeBaseId output referencing the KB', () => {
      const outputs = template.Outputs ?? {};
      const kbOutput = outputs['KnowledgeBaseId'];
      expect(kbOutput).toBeDefined();

      // The value should reference the KB resource (not a hardcoded string)
      const value = kbOutput.Value;
      // CfnKnowledgeBase.attrKnowledgeBaseId resolves to Fn::GetAtt
      if (typeof value === 'object' && 'Fn::GetAtt' in value) {
        const [logicalId, attr] = value['Fn::GetAtt'];
        const resources = template.Resources ?? {};
        expect(resources[logicalId]?.Type).toBe('AWS::Bedrock::KnowledgeBase');
        expect(attr).toBe('KnowledgeBaseId');
      } else {
        // If it's a string, it should not be a hardcoded value
        expect(value).not.toBe('EXAMPLEKBID');
      }
    });
  });
});


// Feature: automate-deployment-tasks, Property 4: VPC is CDK-managed with no hardcoded IDs
// **Validates: Requirements 5.1, 5.7**
describe('Property 4: VPC is CDK-managed with no hardcoded IDs', () => {
  it('template contains an AWS::EC2::VPC resource and no hardcoded vpc-* or vpce-* strings', () => {
    fc.assert(
      fc.property(awsAccountId, (accountId) => {
        const template = synthesizeTemplate(accountId);
        const resources = template.Resources ?? {};

        // Verify at least one AWS::EC2::VPC resource exists (CDK-managed, not imported)
        const vpcResources = Object.entries(resources).filter(
          ([, res]: [string, any]) => res.Type === 'AWS::EC2::VPC'
        );
        expect(vpcResources.length).toBeGreaterThanOrEqual(1);

        // Stringify the entire template and verify no hardcoded VPC IDs or VPC endpoint IDs
        const templateJson = JSON.stringify(template);
        expect(templateJson).not.toMatch(/vpc-[0-9a-f]+/);
        expect(templateJson).not.toMatch(/vpce-[0-9a-f]+/);
      }),
      { numRuns: 100 }
    );
  });
});

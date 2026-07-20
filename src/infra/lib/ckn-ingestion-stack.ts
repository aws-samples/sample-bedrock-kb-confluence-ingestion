import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as aoss from 'aws-cdk-lib/aws-opensearchserverless';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import { Construct } from 'constructs';
import { CloudTrailDetection } from './cloudtrail-detection';

export class CknIngestionStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const schedule = this.node.tryGetContext('schedule') ?? 'cron(0 2 * * ? *)';

    // -----------------------------------------------------------------------
    // 1. ECR Repository
    // -----------------------------------------------------------------------
    const repo = new ecr.Repository(this, 'CknIngestionRepo', {
      repositoryName: 'ckn-ingestion',
      imageScanOnPush: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // -----------------------------------------------------------------------
    // 2. CloudWatch Log Group
    // -----------------------------------------------------------------------
    const logGroup = new logs.LogGroup(this, 'CknIngestionLogGroup', {
      logGroupName: '/ckn/ingestion',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // -----------------------------------------------------------------------
    // 3a. KMS Key (used by S3 bucket and Secrets Manager)
    // -----------------------------------------------------------------------
    const kmsKey = new kms.Key(this, 'CknKmsKey', {
      description: 'CKN Confluence token encryption',
      enableKeyRotation: true,
    });

    // -----------------------------------------------------------------------
    // 3b. SNS Topic for security alerts (encrypted at rest with KMS)
    //     Topic was created outside CloudFormation and encrypted via CLI.
    //     Import by ARN to avoid CloudFormation trying to recreate it.
    // -----------------------------------------------------------------------
    const alertsTopic = sns.Topic.fromTopicArn(
      this,
      'CknSecurityAlertsTopic',
      `arn:aws:sns:${this.region}:${this.account}:ckn-security-alerts`
    );

    // Allow CloudWatch Alarms to publish to the encrypted topic.
    // NOTE: In a KMS key resource policy, `resources: ['*']` refers to the key
    // this policy is attached to (self-reference) — it does NOT grant access to
    // all KMS keys in the account. This is the required/standard form for key
    // policy statements.
    kmsKey.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'AllowCloudWatchAlarmsToUseKey',
      principals: [new iam.ServicePrincipal('cloudwatch.amazonaws.com')],
      actions: ['kms:Decrypt', 'kms:GenerateDataKey*'],
      resources: ['*'],
    }));

    // -----------------------------------------------------------------------
    // 3c. S3 access log bucket (BSC43.1 — server access logging)
    // -----------------------------------------------------------------------
    const accessLogBucket = new s3.Bucket(this, 'CknIngestionAccessLogBucket', {
      bucketName: `ams-ckn-${this.account}-access-logs`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [{ expiration: cdk.Duration.days(90) }],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // -----------------------------------------------------------------------
    // 3d. S3 bucket
    // -----------------------------------------------------------------------
    const bucket = new s3.Bucket(this, 'CknIngestionBucket', {
      bucketName: `ams-ckn-${this.account}`,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: kmsKey,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      serverAccessLogsBucket: accessLogBucket,
      serverAccessLogsPrefix: 'access-logs/',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // -----------------------------------------------------------------------
    // 4. Bedrock KB execution role (used by the manually-created KB)
    // -----------------------------------------------------------------------
    const kbRole = new iam.Role(this, 'CknKbRole', {
      roleName: 'ckn-bedrock-kb-role',
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnLike: { 'aws:SourceArn': `arn:aws:bedrock:${this.region}:${this.account}:knowledge-base/*` },
        },
      }),
      inlinePolicies: {
        KbPolicy: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              sid: 'S3Read',
              actions: ['s3:GetObject', 's3:ListBucket'],
              resources: [bucket.bucketArn, `${bucket.bucketArn}/*`],
            }),
            new iam.PolicyStatement({
              sid: 'KMSDecrypt',
              actions: ['kms:Decrypt'],
              resources: [kmsKey.keyArn],
            }),
            new iam.PolicyStatement({
              sid: 'BedrockEmbed',
              actions: ['bedrock:InvokeModel'],
              resources: [`arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v2:0`],
            }),
            new iam.PolicyStatement({
              sid: 'AossAccess',
              // `aoss:APIAccessAll` is the single data-plane action OpenSearch
              // Serverless exposes for IAM; it cannot be broken into finer verbs.
              // Data-level access is further constrained by the AOSS data access
              // policy, and the resource is scoped to this account's collections.
              actions: ['aoss:APIAccessAll'],
              resources: [`arn:aws:aoss:${this.region}:${this.account}:collection/*`],
            }),
          ],
        }),
      },
    });

    // -----------------------------------------------------------------------
    // 5. VPC + AOSS VPC Endpoint (CDK-managed)
    //    Creates a dedicated VPC with private subnets (NAT gateway egress)
    //    and public subnets. The AOSS VPC endpoint is created within this VPC.
    //    No imported VPC or endpoint IDs — everything is CDK-managed.
    // -----------------------------------------------------------------------
    // Use AZs that support AOSS VPC endpoints (matching deployed infrastructure)
    const aossSupportedAzs = this.region === 'us-east-1'
      ? [`${this.region}a`, `${this.region}b`]
      : undefined;

    const vpc = new ec2.Vpc(this, 'CknIngestionVpc', {
      vpcName: 'ckn-ingestion-vpc',
      ...(aossSupportedAzs ? { availabilityZones: aossSupportedAzs } : { maxAzs: 2 }),
      natGateways: 1,
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
        {
          name: 'Private',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          cidrMask: 24,
        },
      ],
    });

    // AOSS VPC endpoint — must use aoss.CfnVpcEndpoint (not ec2 addInterfaceEndpoint)
    // so that the wildcard private hosted zone is created for collection hostnames.
    const aossVpceSg = new ec2.SecurityGroup(this, 'AossVpceSg', {
      vpc,
      description: 'AOSS VPC endpoint',
      allowAllOutbound: true,
    });
    aossVpceSg.addIngressRule(ec2.Peer.ipv4(vpc.vpcCidrBlock), ec2.Port.tcp(443), 'HTTPS from VPC');

    const aossVpce = new aoss.CfnVpcEndpoint(this, 'AossEndpoint', {
      name: 'ckn-aoss-vpce',
      vpcId: vpc.vpcId,
      subnetIds: vpc.selectSubnets({ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }).subnetIds,
      securityGroupIds: [aossVpceSg.securityGroupId],
    });

    // VPC endpoints to avoid NAT gateway data processing costs
    vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });
    vpc.addInterfaceEndpoint('EcrApiEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.ECR,
      privateDnsEnabled: true,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });
    vpc.addInterfaceEndpoint('EcrDkrEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
      privateDnsEnabled: true,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });
    vpc.addInterfaceEndpoint('CloudWatchLogsEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
      privateDnsEnabled: true,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    new cdk.CfnOutput(this, 'VpcId', {
      value: vpc.vpcId,
      description: 'CDK-managed VPC ID',
    });

    // -----------------------------------------------------------------------
    // 6. OpenSearch Serverless collection + policies
    //    NOTE: The vector index and Bedrock KB are created manually.
    //    See infra/README.md for details.
    // -----------------------------------------------------------------------
    const collectionName = 'ckn-kb-vectors';

    const encryptionPolicy = new aoss.CfnSecurityPolicy(this, 'CknAossEncryption', {
      name: 'ckn-kb-encryption',
      type: 'encryption',
      policy: JSON.stringify({
        Rules: [{ ResourceType: 'collection', Resource: [`collection/${collectionName}`] }],
        AWSOwnedKey: true,
      }),
    });

    const networkPolicy = new aoss.CfnSecurityPolicy(this, 'CknAossNetwork', {
      name: 'ckn-kb-network',
      type: 'network',
      description: 'CKN KB network policy — VPC-only access with Bedrock service access',
      policy: JSON.stringify([
        {
          Rules: [
            { ResourceType: 'collection', Resource: [`collection/${collectionName}`] },
            { ResourceType: 'dashboard', Resource: [`collection/${collectionName}`] },
          ],
          AllowFromPublic: false,
          SourceVPCEs: [aossVpce.attrId],
        },
        {
          Rules: [
            { ResourceType: 'collection', Resource: [`collection/${collectionName}`] },
          ],
          SourceServices: ['bedrock.amazonaws.com'],
        },
      ]),
    });

    // dataAccessPolicy is defined after taskRole (section 8b) so CDK role ARN
    // references resolve correctly.

    const collection = new aoss.CfnCollection(this, 'CknAossCollection', {
      name: collectionName,
      type: 'VECTORSEARCH',
      description: 'CKN Bedrock KB vector store',
    });
    collection.addDependency(encryptionPolicy);
    collection.addDependency(networkPolicy);
    // dataAccessPolicy dependency is added after taskRole (section 8b)

    new cdk.CfnOutput(this, 'CollectionEndpoint', {
      value: collection.attrCollectionEndpoint,
      description: 'AOSS collection endpoint',
    });

    // -----------------------------------------------------------------------
    // 7. Secrets Manager Secret (KMS key defined in section 3a)
    // -----------------------------------------------------------------------
    const confluenceSecret = new secretsmanager.Secret(this, 'CknConfluenceSecret', {
      secretName: 'ams/ckn/confluence-token',
      encryptionKey: kmsKey,
      description: 'Confluence API token - update value post-deployment via AWS Console or CLI',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'confluence-api' }),
        generateStringKey: 'token',
        excludePunctuation: true,
      },
    });

    new cdk.CfnOutput(this, 'KmsKeyArn', {
      value: kmsKey.keyArn,
      description: 'KMS key ARN for Confluence token encryption',
    });

    new cdk.CfnOutput(this, 'ConfluenceSecretArn', {
      value: confluenceSecret.secretArn,
      description: 'Secrets Manager secret ARN for Confluence token',
    });

    // -----------------------------------------------------------------------
    // 8. ECS Cluster + Fargate Task + EventBridge Schedule
    // -----------------------------------------------------------------------
    const cluster = new ecs.Cluster(this, 'CknIngestionCluster', {
      clusterName: 'ckn-ingestion',
      vpc,
      enableFargateCapacityProviders: true,
    });

    const taskRole = new iam.Role(this, 'CknIngestionTaskRole', {
      roleName: 'ckn-ingestion-task-role',
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      inlinePolicies: {
        CknIngestionPolicy: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({ sid: 'SecretsManagerRead', actions: ['secretsmanager:GetSecretValue'], resources: [confluenceSecret.secretArn] }),
            new iam.PolicyStatement({ sid: 'KMSDecrypt', actions: ['kms:Decrypt', 'kms:GenerateDataKey'], resources: [kmsKey.keyArn] }),
            new iam.PolicyStatement({
              sid: 'BedrockInvoke',
              actions: ['bedrock:InvokeModel'],
              // Scoped to the deployment Region (least privilege) rather than a
              // wildcard Region. NOTE: if you switch to a cross-Region inference
              // profile (e.g. `us.anthropic.claude*`), the foundation-model
              // resource must also cover the profile's member Regions — widen
              // these ARNs accordingly for that use case.
              resources: [
                `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude*`,
                `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/us.anthropic.claude*`,
              ],
            }),
            new iam.PolicyStatement({ sid: 'S3Upload', actions: ['s3:PutObject', 's3:PutObjectTagging'], resources: [`${bucket.bucketArn}/confluence/*`] }),
            new iam.PolicyStatement({
              sid: 'AossAccess',
              // `aoss:APIAccessAll` is the single data-plane action OpenSearch
              // Serverless exposes for IAM; it cannot be broken into finer verbs.
              // Data-level access is further constrained by the AOSS data access
              // policy, and the resource is scoped to this account's collections.
              actions: ['aoss:APIAccessAll'],
              resources: [`arn:aws:aoss:${this.region}:${this.account}:collection/*`],
            }),
          ],
        }),
      },
    });

    // -----------------------------------------------------------------------
    // 8b. AOSS Data Access Policy (defined here so kbRole + taskRole ARNs resolve)
    // -----------------------------------------------------------------------
    const dataAccessPolicy = new aoss.CfnAccessPolicy(this, 'CknAossAccess', {
      name: 'ckn-kb-access',
      type: 'data',
      policy: JSON.stringify([{
        Rules: [
          {
            ResourceType: 'collection',
            Resource: [`collection/${collectionName}`],
            Permission: ['aoss:CreateCollectionItems', 'aoss:DeleteCollectionItems', 'aoss:UpdateCollectionItems', 'aoss:DescribeCollectionItems'],
          },
          {
            ResourceType: 'index',
            Resource: [`index/${collectionName}/*`],
            Permission: ['aoss:CreateIndex', 'aoss:DeleteIndex', 'aoss:UpdateIndex', 'aoss:DescribeIndex', 'aoss:ReadDocument', 'aoss:WriteDocument'],
          },
        ],
        Principal: [
          kbRole.roleArn,
          taskRole.roleArn,
        ],
      }]),
    });
    collection.addDependency(dataAccessPolicy);

    const executionRole = new iam.Role(this, 'CknIngestionExecutionRole', {
      roleName: 'ckn-ingestion-execution-role',
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy')],
    });

    // -----------------------------------------------------------------------
    // 9. Index Creator — Fargate task definition
    //    Creates the AOSS vector index. The task definition is managed by CDK;
    //    the task is triggered by a post-deploy script (scripts/create-aoss-index.sh)
    //    that runs on a private subnet in the same VPC as the AOSS VPC endpoint.
    // -----------------------------------------------------------------------
    const indexCreatorTaskDef = new ecs.FargateTaskDefinition(this, 'IndexCreatorTaskDef', {
      family: 'ckn-create-aoss-index',
      cpu: 256,
      memoryLimitMiB: 512,
      taskRole,
      executionRole,
    });

    const indexCreatorPythonScript = [
      'import boto3, json, sys, os',
      'from opensearchpy import OpenSearch, RequestsHttpConnection',
      'from requests_aws4auth import AWS4Auth',
      '',
      'endpoint = os.environ["COLLECTION_ENDPOINT"]',
      '# Strip https:// prefix to get just the host',
      'host = endpoint.replace("https://", "")',
      'region = os.environ.get("AWS_REGION", "us-east-1")',
      '',
      'creds = boto3.Session().get_credentials().get_frozen_credentials()',
      'auth = AWS4Auth(creds.access_key, creds.secret_key, region, "aoss", session_token=creds.token)',
      'client = OpenSearch(',
      '    hosts=[{"host": host, "port": 443}],',
      '    http_auth=auth,',
      '    use_ssl=True,',
      '    verify_certs=True,',
      '    connection_class=RequestsHttpConnection,',
      ')',
      '',
      'index_name = "bedrock-knowledge-base-default-index"',
      '',
      'if client.indices.exists(index=index_name):',
      '    print(f"index already exists: {index_name}")',
      '    sys.exit(0)',
      '',
      'body = {',
      '    "settings": {',
      '        "index.knn": True,',
      '        "number_of_shards": 2,',
      '        "number_of_replicas": 0,',
      '    },',
      '    "mappings": {',
      '        "properties": {',
      '            "bedrock-knowledge-base-default-vector": {',
      '                "type": "knn_vector",',
      '                "dimension": 1024,',
      '                "method": {',
      '                    "engine": "faiss",',
      '                    "name": "hnsw",',
      '                    "parameters": {},',
      '                },',
      '            },',
      '            "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},',
      '            "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False},',
      '        }',
      '    },',
      '}',
      '',
      'try:',
      '    resp = client.indices.create(index=index_name, body=body)',
      '    print(json.dumps(resp, indent=2))',
      'except Exception as e:',
      '    print(f"ERROR creating index: {e}", file=sys.stderr)',
      '    sys.exit(1)',
    ].join('\n');

    const b64Script = Buffer.from(indexCreatorPythonScript).toString('base64');

    indexCreatorTaskDef.addContainer('CreateIndexContainer', {
      containerName: 'create-index',
      image: ecs.ContainerImage.fromEcrRepository(repo, 'index-creator'),
      command: [
        'sh', '-c',
        `echo '${b64Script}' | base64 -d > /tmp/idx.py && pip install -q opensearch-py boto3 requests-aws4auth && python /tmp/idx.py`,
      ],
      environment: {
        COLLECTION_ENDPOINT: collection.attrCollectionEndpoint,
      },
      logging: ecs.LogDrivers.awsLogs({
        logGroup,
        streamPrefix: 'create-index',
      }),
    });

    // The index-creator task is triggered by a post-deploy script (scripts/create-aoss-index.sh)
    // rather than a CDK custom resource. This avoids the Lambda + Provider framework complexity.
    // The Bedrock KB depends on the index existing, so run the script between cdk deploy and
    // the first KB sync.

    // -----------------------------------------------------------------------
    // 10. Bedrock Knowledge Base + S3 Data Source
    //     Uses L1 constructs (CfnKnowledgeBase / CfnDataSource).
    //     Skipped on first deploy (--context deployKb=true to enable).
    //     The AOSS vector index must exist before the KB can be created.
    // -----------------------------------------------------------------------
    const deployKb = this.node.tryGetContext('deployKb') === 'true';

    if (deployKb) {
      const kb = new bedrock.CfnKnowledgeBase(this, 'CknKnowledgeBase', {
        name: 'ckn-knowledge-base',
        roleArn: kbRole.roleArn,
        knowledgeBaseConfiguration: {
          type: 'VECTOR',
          vectorKnowledgeBaseConfiguration: {
            embeddingModelArn: `arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v2:0`,
          },
        },
        storageConfiguration: {
          type: 'OPENSEARCH_SERVERLESS',
          opensearchServerlessConfiguration: {
            collectionArn: collection.attrArn,
            vectorIndexName: 'bedrock-knowledge-base-default-index',
            fieldMapping: {
              vectorField: 'bedrock-knowledge-base-default-vector',
              textField: 'AMAZON_BEDROCK_TEXT_CHUNK',
              metadataField: 'AMAZON_BEDROCK_METADATA',
            },
          },
        },
      });

      const dataSource = new bedrock.CfnDataSource(this, 'CknKbDataSource', {
        knowledgeBaseId: kb.attrKnowledgeBaseId,
        name: 'ckn-confluence-s3',
        dataSourceConfiguration: {
          type: 'S3',
          s3Configuration: {
            bucketArn: bucket.bucketArn,
            inclusionPrefixes: ['confluence/'],
          },
        },
        vectorIngestionConfiguration: {
          chunkingConfiguration: {
            chunkingStrategy: 'SEMANTIC',
            semanticChunkingConfiguration: {
              maxTokens: 500,
              breakpointPercentileThreshold: 95,
              bufferSize: 1,
            },
          },
        },
      });

      new cdk.CfnOutput(this, 'KnowledgeBaseId', {
        value: kb.attrKnowledgeBaseId,
        description: 'Bedrock Knowledge Base ID',
      });
    }

    const taskDef = new ecs.FargateTaskDefinition(this, 'CknIngestionTaskDef', {
      family: 'ckn-ingestion', cpu: 4096, memoryLimitMiB: 16384, taskRole, executionRole,
    });
    taskDef.addContainer('CknIngestionContainer', {
      containerName: 'ckn-ingestion',
      image: ecs.ContainerImage.fromEcrRepository(repo, 'latest'),
      command: ['python', '-m', 'ckn_ingestion'],
      logging: ecs.LogDrivers.awsLogs({ logGroup, streamPrefix: 'ckn-ingestion' }),
    });

    const eventRole = new iam.Role(this, 'CknIngestionEventRole', {
      roleName: 'ckn-ingestion-event-role',
      assumedBy: new iam.ServicePrincipal('events.amazonaws.com'),
      inlinePolicies: {
        EcsRunTask: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({ actions: ['ecs:RunTask'], resources: [taskDef.taskDefinitionArn] }),
            new iam.PolicyStatement({ actions: ['iam:PassRole'], resources: [taskRole.roleArn, executionRole.roleArn] }),
          ],
        }),
      },
    });

    new events.Rule(this, 'CknIngestionSchedule', {
      ruleName: 'ckn-ingestion-daily',
      schedule: events.Schedule.expression(schedule),
      targets: [new targets.EcsTask({
        cluster,
        taskDefinition: taskDef,
        taskCount: 1,
        launchType: ecs.LaunchType.FARGATE,
        platformVersion: ecs.FargatePlatformVersion.LATEST,
        role: eventRole,
        subnetSelection: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
        assignPublicIp: false,
      })],
    });

    // -----------------------------------------------------------------------
    // 11. CloudTrail Detection — security monitoring alarms
    // -----------------------------------------------------------------------
    new CloudTrailDetection(this, 'CloudTrailDetection', {
      cloudTrailLogGroupName: '/aws/cloudtrail/ckn-trail',
      serviceRoleArns: [
        taskRole.roleArn,
        executionRole.roleArn,
        kbRole.roleArn,
        eventRole.roleArn,
      ],
      allowlistPrincipalArns: [],
      alarmActionArn: alertsTopic.topicArn,
    });
  }
}

import { Construct } from 'constructs';
import * as cdk from 'aws-cdk-lib';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatch_actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';

/**
 * Severity levels for detection alarms.
 */
export enum AlarmSeverity {
  HIGH = 'HIGH',
  MEDIUM = 'MEDIUM',
}

/**
 * Configuration for a single detection rule (final form with built filter pattern).
 */
export interface DetectionRule {
  /** Unique identifier for the rule (used in metric/alarm naming) */
  readonly id: string;
  /** Human-readable description of what the rule detects */
  readonly description: string;
  /** CloudWatch Metric Filter pattern string */
  readonly filterPattern: string;
  /** Alarm severity level */
  readonly severity: AlarmSeverity;
}

/**
 * The type of filter pattern to build for a detection rule.
 * - 'eventName': Standard eventName-based matching with allowlist exclusion
 * - 'guardduty': Special pattern including requestParameters.enable IS FALSE for UpdateDetector
 * - 'stsRecon': Pattern matching STS GetCallerIdentity from specific service roles
 */
export type DetectionRuleType = 'eventName' | 'guardduty' | 'stsRecon';

/**
 * Static configuration for a detection rule before filter pattern is built.
 * Used to define the DETECTION_RULES array that drives construct creation.
 */
export interface DetectionRuleConfig {
  /** Unique identifier for the rule (used in metric/alarm naming) */
  readonly id: string;
  /** Human-readable description of what the rule detects */
  readonly description: string;
  /** CloudTrail event names to match (not used for stsRecon type) */
  readonly eventNames: string[];
  /** Alarm severity level */
  readonly severity: AlarmSeverity;
  /** Determines how the filter pattern is built */
  readonly type: DetectionRuleType;
}

/**
 * Static detection rules configuration.
 * Each rule defines what CloudTrail events to detect and at what severity.
 * The filter pattern is built at construct time based on the rule type.
 */
export const DETECTION_RULES: readonly DetectionRuleConfig[] = [
  {
    id: 'cloudtrail-tampering',
    description: 'Detects attempts to disable or modify CloudTrail logging',
    eventNames: ['DeleteTrail', 'StopLogging', 'UpdateTrail'],
    severity: AlarmSeverity.HIGH,
    type: 'eventName',
  },
  {
    id: 'iam-privilege-escalation',
    description: 'Detects unauthorized IAM changes that could escalate privileges',
    eventNames: [
      'AttachRolePolicy',
      'CreateRole',
      'CreateUser',
      'PutRolePolicy',
      'PutUserPolicy',
      'AttachUserPolicy',
      'DetachRolePolicy',
      'DeleteRolePolicy',
    ],
    severity: AlarmSeverity.HIGH,
    type: 'eventName',
  },
  {
    id: 'guardduty-disabling',
    description: 'Detects attempts to disable GuardDuty threat detection',
    eventNames: [
      'DeleteDetector',
      'StopMonitoringMembers',
      'DisassociateMembers',
      'UpdateDetector',
    ],
    severity: AlarmSeverity.HIGH,
    type: 'guardduty',
  },
  {
    id: 'vpc-network-changes',
    description: 'Detects unauthorized VPC network changes that could create data exfiltration paths',
    eventNames: [
      'AttachInternetGateway',
      'CreateRoute',
      'ReplaceRoute',
      'CreateVpcPeeringConnection',
      'AcceptVpcPeeringConnection',
      'CreateNatGateway',
      'CreateVpcEndpoint',
      'ModifyVpcAttribute',
    ],
    severity: AlarmSeverity.HIGH,
    type: 'eventName',
  },
  {
    id: 'security-group-changes',
    description: 'Detects unauthorized security group modifications',
    eventNames: [
      'AuthorizeSecurityGroupIngress',
      'AuthorizeSecurityGroupEgress',
      'RevokeSecurityGroupIngress',
      'RevokeSecurityGroupEgress',
      'CreateSecurityGroup',
      'DeleteSecurityGroup',
    ],
    severity: AlarmSeverity.MEDIUM,
    type: 'eventName',
  },
  {
    id: 'sts-recon',
    description: 'Detects STS GetCallerIdentity calls from CKN service roles indicating credential theft reconnaissance',
    eventNames: ['GetCallerIdentity'],
    severity: AlarmSeverity.HIGH,
    type: 'stsRecon',
  },
];

/**
 * Props for the CloudTrailDetection construct.
 */
export interface CloudTrailDetectionProps {
  /**
   * The CloudWatch Log Group name where CloudTrail delivers logs.
   * Must be 1–512 characters.
   */
  readonly cloudTrailLogGroupName: string;

  /**
   * ARNs of the CKN service IAM roles to monitor for STS recon detection.
   * Must contain 1–10 entries. Each must be a valid IAM role ARN.
   */
  readonly serviceRoleArns: string[];

  /**
   * ARNs of deployment principals to exclude from detection rules.
   * 0–20 entries. These are CDK/CloudFormation roles that perform
   * legitimate infrastructure changes.
   */
  readonly allowlistPrincipalArns: string[];

  /**
   * The SNS topic ARN or CloudWatch alarm action ARN for notifications.
   * Must be a valid ARN.
   */
  readonly alarmActionArn: string;

  /**
   * Optional: Custom metric namespace. Defaults to 'CKN/Security'.
   */
  readonly metricNamespace?: string;
}

/**
 * CDK construct that monitors CloudTrail logs for suspicious API activity
 * against the CKN ingestion service infrastructure.
 *
 * Creates 6 CloudWatch Metric Filters and 6 CloudWatch Alarms that detect:
 * - CloudTrail tampering
 * - IAM privilege escalation
 * - GuardDuty disabling
 * - VPC network changes
 * - Security group changes
 * - STS reconnaissance from service roles
 */
export class CloudTrailDetection extends Construct {
  /** The 6 CloudWatch Alarms created by this construct */
  public readonly alarms: cloudwatch.Alarm[];

  constructor(scope: Construct, id: string, props: CloudTrailDetectionProps) {
    super(scope, id);

    this.validateProps(props);

    const metricNamespace = props.metricNamespace ?? 'CKN/Security';
    const allowlist = props.allowlistPrincipalArns ?? [];
    const alarmsTmp: cloudwatch.Alarm[] = [];

    // Import the CloudTrail log group by name
    const logGroup = logs.LogGroup.fromLogGroupName(
      this,
      'CloudTrailLogGroup',
      props.cloudTrailLogGroupName
    );

    // Import the SNS topic for alarm actions
    const alarmTopic = sns.Topic.fromTopicArn(
      this,
      'AlarmTopic',
      props.alarmActionArn
    );

    // Iterate over each detection rule and create metric filter + alarm
    for (const ruleConfig of DETECTION_RULES) {
      // Build the filter pattern based on rule type
      let filterPattern: string;
      switch (ruleConfig.type) {
        case 'eventName':
          filterPattern = this.buildEventNameFilterPattern(
            ruleConfig.eventNames,
            allowlist
          );
          break;
        case 'guardduty':
          filterPattern = this.buildGuardDutyFilterPattern(
            ruleConfig.eventNames,
            allowlist
          );
          break;
        case 'stsRecon':
          filterPattern = this.buildStsReconFilterPattern(
            props.serviceRoleArns
          );
          break;
      }

      // Create the metric filter
      const metricFilter = new logs.MetricFilter(this, `MetricFilter-${ruleConfig.id}`, {
        logGroup,
        filterPattern: logs.FilterPattern.literal(filterPattern),
        metricNamespace,
        metricName: ruleConfig.id,
        metricValue: '1',
        defaultValue: 0,
      });

      // Create the alarm
      const alarm = new cloudwatch.Alarm(this, `Alarm-${ruleConfig.id}`, {
        metric: new cloudwatch.Metric({
          namespace: metricNamespace,
          metricName: ruleConfig.id,
          statistic: 'Sum',
          period: cdk.Duration.seconds(300),
        }),
        threshold: 1,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        alarmDescription: `[${ruleConfig.severity}] ${ruleConfig.id}: ${ruleConfig.description}`,
        alarmName: `${id}-${ruleConfig.id}`,
      });

      // Add the SNS topic as an alarm action
      alarm.addAlarmAction(new cloudwatch_actions.SnsAction(alarmTopic));

      alarmsTmp.push(alarm);
    }

    this.alarms = alarmsTmp;
  }

  /**
   * Builds the metric filter pattern for a given set of eventNames,
   * excluding allowlisted principals.
   *
   * Pattern format with allowlist:
   *   { ($.eventName = "X" || $.eventName = "Y") && $.userIdentity.arn != "ARN1" && $.userIdentity.arn != "ARN2" }
   *
   * Pattern format without allowlist:
   *   { ($.eventName = "X" || $.eventName = "Y") }
   *
   * @throws Error if the resulting pattern exceeds 1024 characters
   */
  private buildEventNameFilterPattern(
    eventNames: string[],
    allowlist: string[]
  ): string {
    // Build the eventName OR clauses inside parentheses
    const eventClauses = eventNames
      .map((name) => `$.eventName = "${name}"`)
      .join(' || ');

    let pattern: string;

    if (allowlist.length === 0) {
      // No exclusion clauses needed
      pattern = `{ (${eventClauses}) }`;
    } else {
      // Append != clauses for each allowlisted principal
      const exclusionClauses = allowlist
        .map((arn) => `$.userIdentity.arn != "${arn}"`)
        .join(' && ');

      pattern = `{ (${eventClauses}) && ${exclusionClauses} }`;
    }

    // CloudWatch Metric Filter patterns have a 1024-character limit
    if (pattern.length > 1024) {
      throw new Error(
        `CloudTrailDetection: metric filter pattern exceeds 1024-character limit (${pattern.length} characters). Reduce the number of event names or allowlist entries.`
      );
    }

    return pattern;
  }

  /**
   * Builds the GuardDuty detection filter pattern.
   *
   * This handles the special case where UpdateDetector must also check
   * requestParameters.enable IS FALSE, while other GuardDuty events
   * (DeleteDetector, StopMonitoringMembers, DisassociateMembers) are
   * matched by eventName alone.
   *
   * Pattern format:
   *   { (($.eventName = "DeleteDetector" || ...) || ($.eventName = "UpdateDetector" && $.requestParameters.enable IS FALSE)) && $.userIdentity.arn != "ARN1" }
   */
  private buildGuardDutyFilterPattern(
    eventNames: string[],
    allowlist: string[]
  ): string {
    // Separate UpdateDetector from the other event names
    const standardEvents = eventNames.filter((name) => name !== 'UpdateDetector');
    const hasUpdateDetector = eventNames.includes('UpdateDetector');

    // Build the standard eventName OR clauses
    const standardClauses = standardEvents
      .map((name) => `$.eventName = "${name}"`)
      .join(' || ');

    // Build the combined event matching clause
    let eventMatchClause: string;
    if (hasUpdateDetector && standardEvents.length > 0) {
      eventMatchClause = `((${standardClauses}) || ($.eventName = "UpdateDetector" && $.requestParameters.enable IS FALSE))`;
    } else if (hasUpdateDetector) {
      eventMatchClause = `($.eventName = "UpdateDetector" && $.requestParameters.enable IS FALSE)`;
    } else {
      eventMatchClause = `(${standardClauses})`;
    }

    let pattern: string;

    if (allowlist.length === 0) {
      pattern = `{ ${eventMatchClause} }`;
    } else {
      const exclusionClauses = allowlist
        .map((arn) => `$.userIdentity.arn != "${arn}"`)
        .join(' && ');

      pattern = `{ ${eventMatchClause} && ${exclusionClauses} }`;
    }

    // CloudWatch Metric Filter patterns have a 1024-character limit
    if (pattern.length > 1024) {
      throw new Error(
        `CloudTrailDetection: metric filter pattern exceeds 1024-character limit (${pattern.length} characters). Reduce the number of event names or allowlist entries.`
      );
    }

    return pattern;
  }

  /**
   * Builds the STS recon filter pattern matching specific role ARNs
   * in the userIdentity field.
   *
   * Produces a pattern like:
   * { $.eventSource = "sts.amazonaws.com" && $.eventName = "GetCallerIdentity" && ($.userIdentity.arn = "*role-name*" || ...) }
   */
  private buildStsReconFilterPattern(
    serviceRoleArns: string[]
  ): string {
    // Extract role names from full ARNs (the part after the last '/')
    const roleNames = serviceRoleArns.map((arn) => {
      const lastSlashIndex = arn.lastIndexOf('/');
      return lastSlashIndex >= 0 ? arn.substring(lastSlashIndex + 1) : arn;
    });

    // Build OR clauses for each role name using wildcard matching
    const roleMatchClauses = roleNames
      .map((name) => `$.userIdentity.arn = "*${name}*"`)
      .join(' || ');

    return `{ $.eventSource = "sts.amazonaws.com" && $.eventName = "GetCallerIdentity" && (${roleMatchClauses}) }`;
  }

  /**
   * Validates construct props and throws descriptive errors for invalid inputs.
   */
  private validateProps(props: CloudTrailDetectionProps): void {
    // Validate cloudTrailLogGroupName: must be 1–512 characters
    if (
      !props.cloudTrailLogGroupName ||
      props.cloudTrailLogGroupName.length === 0 ||
      props.cloudTrailLogGroupName.length > 512
    ) {
      throw new Error(
        'CloudTrailDetection: cloudTrailLogGroupName must be 1–512 characters'
      );
    }

    // Validate serviceRoleArns: must contain 1–10 entries
    if (
      !props.serviceRoleArns ||
      props.serviceRoleArns.length === 0 ||
      props.serviceRoleArns.length > 10
    ) {
      throw new Error(
        'CloudTrailDetection: serviceRoleArns must contain 1–10 entries'
      );
    }

    // Validate allowlistPrincipalArns: must contain 0–20 entries
    if (props.allowlistPrincipalArns && props.allowlistPrincipalArns.length > 20) {
      throw new Error(
        'CloudTrailDetection: allowlistPrincipalArns must contain 0–20 entries'
      );
    }

    // Validate alarmActionArn: must be a non-empty string
    if (!props.alarmActionArn || props.alarmActionArn.length === 0) {
      throw new Error(
        'CloudTrailDetection: alarmActionArn must be a non-empty string'
      );
    }

    // Validate ARN format for all ARNs in serviceRoleArns (skip unresolved tokens)
    for (const arn of props.serviceRoleArns) {
      if (!cdk.Token.isUnresolved(arn) && !arn.startsWith('arn:aws:')) {
        throw new Error(
          `CloudTrailDetection: invalid ARN format in serviceRoleArns: ${arn}`
        );
      }
    }

    // Validate ARN format for all ARNs in allowlistPrincipalArns (skip unresolved tokens)
    if (props.allowlistPrincipalArns) {
      for (const arn of props.allowlistPrincipalArns) {
        if (!cdk.Token.isUnresolved(arn) && !arn.startsWith('arn:aws:')) {
          throw new Error(
            `CloudTrailDetection: invalid ARN format in allowlistPrincipalArns: ${arn}`
          );
        }
      }
    }

    // Validate ARN format for alarmActionArn (skip unresolved tokens)
    if (!cdk.Token.isUnresolved(props.alarmActionArn) && !props.alarmActionArn.startsWith('arn:aws:')) {
      throw new Error(
        `CloudTrailDetection: invalid ARN format in alarmActionArn: ${props.alarmActionArn}`
      );
    }
  }
}

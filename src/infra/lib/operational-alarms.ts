import { Construct } from 'constructs';
import * as cdk from 'aws-cdk-lib';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatch_actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';

/**
 * Props for the OperationalAlarms construct.
 */
export interface OperationalAlarmsProps {
  /**
   * The CloudWatch Log Group the ingestion task writes to
   * (e.g. `/ckn/ingestion`). Metric filters are attached to this group.
   */
  readonly logGroup: logs.ILogGroup;

  /**
   * SNS topic ARN to notify when an alarm fires.
   */
  readonly alarmTopicArn: string;

  /**
   * How many hours without a successful run before the absence-of-success
   * (heartbeat) alarm fires. Implemented as an M-of-N alarm over 1-hour
   * periods (evaluationPeriods = datapointsToAlarm = heartbeatHours), so the
   * alarm trips only when EVERY hour in the trailing window lacked a success.
   *
   * Must be between 1 and 24. CloudWatch caps an alarm's total evaluation
   * range (Period x EvaluationPeriods) at 86,400 s (24 h), so 24 is the hard
   * maximum. For a daily schedule this window is necessarily tight; if you
   * need more jitter headroom, lengthen the ingestion schedule interval rather
   * than the window.
   *
   * @default 24
   */
  readonly heartbeatHours?: number;

  /**
   * Metric namespace for the operational metrics.
   *
   * @default 'CKN/Operations'
   */
  readonly metricNamespace?: string;
}

/**
 * Operational alerting for the CKN ingestion pipeline (addresses the gap
 * where the pipeline ran unnoticed for a month, then stopped unnoticed for a
 * month, with zero signal in either direction).
 *
 * Creates two CloudWatch alarms wired to an SNS topic:
 *
 *  1. **Run failure** — fires when the ingestion task logs an unhandled error
 *     (the centralized handler in `cli.py::main` logs
 *     `Unhandled error during ingestion` then exits non-zero).
 *
 *  2. **Absence of success (heartbeat)** — fires when NO successful run
 *     completion (`INGESTION_RUN_COMPLETE`, emitted at the end of a clean run)
 *     is observed within `heartbeatHours`. This catches both "never ran"
 *     (disabled schedule, broken trigger) and "ran but never finished
 *     cleanly", because missing data is treated as breaching.
 *
 * Both alarms read from the ingestion log group via metric filters, so no
 * additional IAM (e.g. cloudwatch:PutMetricData) is required by the task.
 */
export class OperationalAlarms extends Construct {
  /** The alarms created by this construct (failure, heartbeat). */
  public readonly alarms: cloudwatch.Alarm[];

  constructor(scope: Construct, id: string, props: OperationalAlarmsProps) {
    super(scope, id);

    const heartbeatHours = props.heartbeatHours ?? 24;
    if (!Number.isInteger(heartbeatHours) || heartbeatHours < 1 || heartbeatHours > 24) {
      // CloudWatch caps Period x EvaluationPeriods at 86,400 s (24 h). With a
      // 1-hour period, that bounds the window at 24 evaluation periods.
      throw new Error('OperationalAlarms: heartbeatHours must be an integer between 1 and 24');
    }
    const namespace = props.metricNamespace ?? 'CKN/Operations';

    const alarmTopic = sns.Topic.fromTopicArn(this, 'AlarmTopic', props.alarmTopicArn);

    // -----------------------------------------------------------------------
    // 1. Run-failure alarm
    // -----------------------------------------------------------------------
    const failureMetricName = 'IngestionRunFailure';
    new logs.MetricFilter(this, 'RunFailureFilter', {
      logGroup: props.logGroup,
      // The centralized error handler logs this exact phrase before exit(1).
      filterPattern: logs.FilterPattern.literal('"Unhandled error during ingestion"'),
      metricNamespace: namespace,
      metricName: failureMetricName,
      metricValue: '1',
      defaultValue: 0,
    });

    const failureAlarm = new cloudwatch.Alarm(this, 'RunFailureAlarm', {
      alarmName: `${id}-run-failure`,
      alarmDescription:
        '[HIGH] CKN ingestion task logged an unhandled error and exited non-zero.',
      metric: new cloudwatch.Metric({
        namespace,
        metricName: failureMetricName,
        statistic: 'Sum',
        period: cdk.Duration.hours(1),
      }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      // A failure is a discrete event; absence of failures is the healthy state.
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    failureAlarm.addAlarmAction(new cloudwatch_actions.SnsAction(alarmTopic));

    // -----------------------------------------------------------------------
    // 2. Absence-of-success (heartbeat) alarm
    // -----------------------------------------------------------------------
    const successMetricName = 'IngestionRunSuccess';
    new logs.MetricFilter(this, 'RunSuccessFilter', {
      logGroup: props.logGroup,
      // Emitted once at the end of a clean run (see cli.py step 12).
      filterPattern: logs.FilterPattern.literal('"INGESTION_RUN_COMPLETE"'),
      metricNamespace: namespace,
      metricName: successMetricName,
      metricValue: '1',
      // No defaultValue: we want genuinely-missing data (no run) to read as
      // missing so the heartbeat alarm's treatMissingData=BREACHING triggers.
    });

    const heartbeatAlarm = new cloudwatch.Alarm(this, 'RunHeartbeatAlarm', {
      alarmName: `${id}-no-successful-run`,
      alarmDescription:
        `[HIGH] No successful CKN ingestion run in the last ${heartbeatHours}h ` +
        '(pipeline did not run, or did not complete cleanly).',
      // M-of-N over 1-hour periods: fire only when ALL `heartbeatHours` trailing
      // hours lacked a success. CloudWatch caps Period x EvaluationPeriods at
      // 86,400 s, so a single long period (e.g. 26 h) is invalid and rejected at
      // deploy time — hence 1-hour periods with N evaluation periods instead.
      metric: new cloudwatch.Metric({
        namespace,
        metricName: successMetricName,
        statistic: 'Sum',
        period: cdk.Duration.hours(1),
      }),
      threshold: 1,
      evaluationPeriods: heartbeatHours,
      datapointsToAlarm: heartbeatHours,
      // Fire when the success count is BELOW 1 in an hourly bucket...
      comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
      // ...and, critically, when there is no data at all (nothing ran).
      treatMissingData: cloudwatch.TreatMissingData.BREACHING,
    });
    heartbeatAlarm.addAlarmAction(new cloudwatch_actions.SnsAction(alarmTopic));

    this.alarms = [failureAlarm, heartbeatAlarm];
  }
}

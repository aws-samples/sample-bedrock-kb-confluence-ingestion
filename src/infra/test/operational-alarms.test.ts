import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import * as logs from 'aws-cdk-lib/aws-logs';
import { OperationalAlarms } from '../lib/operational-alarms';

/**
 * Synthesize a throwaway stack containing only the OperationalAlarms construct
 * (plus a log group for it to attach metric filters to) and return the template.
 */
function synth(props?: { heartbeatHours?: number }): Template {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'TestStack', {
    env: { account: '123456789012', region: 'us-east-1' },
  });
  const logGroup = new logs.LogGroup(stack, 'LogGroup', {
    logGroupName: '/ckn/ingestion',
  });
  new OperationalAlarms(stack, 'OperationalAlarms', {
    logGroup,
    alarmTopicArn: 'arn:aws:sns:us-east-1:123456789012:ckn-security-alerts',
    heartbeatHours: props?.heartbeatHours,
  });
  return Template.fromStack(stack);
}

describe('OperationalAlarms', () => {
  it('creates exactly two alarms and two metric filters', () => {
    const t = synth();
    t.resourceCountIs('AWS::CloudWatch::Alarm', 2);
    t.resourceCountIs('AWS::Logs::MetricFilter', 2);
  });

  it('run-failure metric filter keys off the unhandled-error phrase', () => {
    const t = synth();
    t.hasResourceProperties('AWS::Logs::MetricFilter', {
      FilterPattern: '"Unhandled error during ingestion"',
    });
  });

  it('success metric filter keys off the run-completion marker', () => {
    const t = synth();
    t.hasResourceProperties('AWS::Logs::MetricFilter', {
      FilterPattern: '"INGESTION_RUN_COMPLETE"',
    });
  });

  it('failure alarm treats missing data as NOT breaching (absence of failures is healthy)', () => {
    const t = synth();
    t.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'OperationalAlarms-run-failure',
      TreatMissingData: 'notBreaching',
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
      Threshold: 1,
    });
  });

  it('heartbeat alarm treats missing data as BREACHING (no run = alarm)', () => {
    const t = synth();
    t.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'OperationalAlarms-no-successful-run',
      TreatMissingData: 'breaching',
      ComparisonOperator: 'LessThanThreshold',
      Threshold: 1,
    });
  });

  it('heartbeat alarm is M-of-N over 1-hour periods (Period x EvaluationPeriods within CW 24h cap)', () => {
    const t = synth({ heartbeatHours: 24 });
    t.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'OperationalAlarms-no-successful-run',
      Period: 3600,
      EvaluationPeriods: 24,
      DatapointsToAlarm: 24,
    });
  });

  it('heartbeat alarm evaluation window never exceeds CloudWatch 86,400s cap', () => {
    // Default and boundary must both stay within Period x EvaluationPeriods <= 86,400.
    for (const hours of [undefined, 1, 12, 24]) {
      const t = synth({ heartbeatHours: hours });
      const alarms = t.findResources('AWS::CloudWatch::Alarm');
      for (const [, alarm] of Object.entries(alarms)) {
        const p = (alarm as any).Properties;
        expect(p.Period * p.EvaluationPeriods).toBeLessThanOrEqual(86_400);
      }
    }
  });

  it('both alarms publish to the provided SNS topic', () => {
    const t = synth();
    const topic = 'arn:aws:sns:us-east-1:123456789012:ckn-security-alerts';
    // Every alarm should list the topic in AlarmActions.
    const alarms = t.findResources('AWS::CloudWatch::Alarm');
    for (const [, alarm] of Object.entries(alarms)) {
      expect((alarm as any).Properties.AlarmActions).toContainEqual(topic);
    }
  });

  it('rejects heartbeatHours outside 1..24', () => {
    expect(() => synth({ heartbeatHours: 0 })).toThrow(/between 1 and 24/);
    expect(() => synth({ heartbeatHours: 25 })).toThrow(/between 1 and 24/);
    expect(() => synth({ heartbeatHours: 26 })).toThrow(/between 1 and 24/);
  });
});

import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import * as fc from 'fast-check';
import { CloudTrailDetection, DETECTION_RULES } from '../lib/cloudtrail-detection';

/**
 * Unit tests for the buildEventNameFilterPattern method.
 * Tests the metric filter pattern builder for eventName-based detection rules.
 */
describe('CloudTrailDetection - buildEventNameFilterPattern', () => {
  let construct: CloudTrailDetection;

  beforeEach(() => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'TestStack');
    construct = new CloudTrailDetection(stack, 'TestDetection', {
      cloudTrailLogGroupName: '/aws/cloudtrail/test-trail',
      serviceRoleArns: ['arn:aws:iam::123456789012:role/test-role'],
      allowlistPrincipalArns: [],
      alarmActionArn: 'arn:aws:sns:us-east-1:123456789012:test-topic',
    });
  });

  it('builds pattern with event names and no allowlist', () => {
    const pattern = (construct as any).buildEventNameFilterPattern(
      ['DeleteTrail', 'StopLogging', 'UpdateTrail'],
      []
    );

    expect(pattern).toBe(
      '{ ($.eventName = "DeleteTrail" || $.eventName = "StopLogging" || $.eventName = "UpdateTrail") }'
    );
  });

  it('builds pattern with event names and allowlist', () => {
    const pattern = (construct as any).buildEventNameFilterPattern(
      ['DeleteTrail', 'StopLogging'],
      [
        'arn:aws:iam::123456789012:role/cdk-cfn-exec',
        'arn:aws:iam::123456789012:role/cdk-deploy',
      ]
    );

    expect(pattern).toBe(
      '{ ($.eventName = "DeleteTrail" || $.eventName = "StopLogging") && $.userIdentity.arn != "arn:aws:iam::123456789012:role/cdk-cfn-exec" && $.userIdentity.arn != "arn:aws:iam::123456789012:role/cdk-deploy" }'
    );
  });

  it('builds pattern with a single event name and single allowlist entry', () => {
    const pattern = (construct as any).buildEventNameFilterPattern(
      ['DeleteTrail'],
      ['arn:aws:iam::123456789012:role/cdk-exec']
    );

    expect(pattern).toBe(
      '{ ($.eventName = "DeleteTrail") && $.userIdentity.arn != "arn:aws:iam::123456789012:role/cdk-exec" }'
    );
  });

  it('throws error when pattern exceeds 1024 characters', () => {
    // Generate enough allowlist entries to exceed the 1024-char limit
    const longAllowlist = Array.from({ length: 20 }, (_, i) =>
      `arn:aws:iam::123456789012:role/very-long-role-name-that-takes-up-space-${i.toString().padStart(3, '0')}`
    );

    expect(() =>
      (construct as any).buildEventNameFilterPattern(
        ['AttachInternetGateway', 'CreateRoute', 'ReplaceRoute', 'CreateVpcPeeringConnection', 'AcceptVpcPeeringConnection', 'CreateNatGateway', 'CreateVpcEndpoint', 'ModifyVpcAttribute'],
        longAllowlist
      )
    ).toThrow(/exceeds 1024-character limit/);
  });

  it('handles many event names without allowlist', () => {
    const eventNames = [
      'AttachRolePolicy', 'CreateRole', 'CreateUser',
      'PutRolePolicy', 'PutUserPolicy', 'AttachUserPolicy',
      'DetachRolePolicy', 'DeleteRolePolicy',
    ];

    const pattern = (construct as any).buildEventNameFilterPattern(eventNames, []);

    // Verify all event names are present
    for (const name of eventNames) {
      expect(pattern).toContain(`$.eventName = "${name}"`);
    }

    // Verify pattern structure
    expect(pattern).toMatch(/^\{ \(.*\) \}$/);
    // Verify no exclusion clauses
    expect(pattern).not.toContain('$.userIdentity.arn !=');
  });

  it('includes all allowlist ARNs as exclusion clauses', () => {
    const allowlist = [
      'arn:aws:iam::111111111111:role/role-a',
      'arn:aws:iam::222222222222:role/role-b',
      'arn:aws:iam::333333333333:role/role-c',
    ];

    const pattern = (construct as any).buildEventNameFilterPattern(
      ['DeleteTrail'],
      allowlist
    );

    for (const arn of allowlist) {
      expect(pattern).toContain(`$.userIdentity.arn != "${arn}"`);
    }
  });

  it('pattern is enclosed in curly braces', () => {
    const pattern = (construct as any).buildEventNameFilterPattern(
      ['StopLogging'],
      ['arn:aws:iam::123456789012:role/test']
    );

    expect(pattern.startsWith('{ ')).toBe(true);
    expect(pattern.endsWith(' }')).toBe(true);
  });

  it('event names are joined with || inside parentheses', () => {
    const pattern = (construct as any).buildEventNameFilterPattern(
      ['EventA', 'EventB', 'EventC'],
      []
    );

    expect(pattern).toContain(
      '($.eventName = "EventA" || $.eventName = "EventB" || $.eventName = "EventC")'
    );
  });
});


/**
 * Property 2: Allowlist exclusion completeness
 *
 * For any non-empty allowlist of 1–20 valid IAM ARNs, every metric filter pattern
 * (for rules that use the allowlist) SHALL contain a != exclusion for each ARN
 * in the allowlist, ensuring no allowlisted principal can trigger any detection rule.
 *
 * **Validates: Requirements 7.2, 7.3**
 */
describe('CloudTrailDetection - Property 2: Allowlist exclusion completeness', () => {
  // Create a single construct instance to reuse across property iterations.
  // The buildEventNameFilterPattern and buildGuardDutyFilterPattern methods are
  // pure functions that only depend on their arguments, so we can safely reuse
  // the construct instance.
  let construct: CloudTrailDetection;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'Prop2Stack');
    construct = new CloudTrailDetection(stack, 'Prop2Detection', {
      cloudTrailLogGroupName: '/aws/cloudtrail/test-trail',
      serviceRoleArns: ['arn:aws:iam::123456789012:role/test-role'],
      allowlistPrincipalArns: [],
      alarmActionArn: 'arn:aws:sns:us-east-1:123456789012:test-topic',
    });
  });

  // Generator for valid IAM role ARNs with short names to stay under the
  // 1024-char CloudWatch Metric Filter pattern limit.
  // Each exclusion clause: '$.userIdentity.arn != "arn:aws:iam::XXXXXXXXXXXX:role/XXXXX" && '
  // is approximately 65 chars. The vpc-network-changes rule has the longest
  // base pattern (~310 chars), leaving ~714 chars for exclusions.
  // With 10 entries × ~65 chars = 650 chars, safely under the limit.
  const iamRoleArnProp2 = fc
    .tuple(
      fc.stringMatching(/^[0-9]{12}$/),
      fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9]{0,3}$/)
    )
    .map(([account, name]) => `arn:aws:iam::${account}:role/${name}`);

  // Generator for allowlists of 1–10 valid IAM ARNs (constrained to stay
  // within the 1024-char pattern limit for the longest detection rule)
  const allowlistArb = fc.array(iamRoleArnProp2, { minLength: 1, maxLength: 10 });

  it('every eventName-based filter pattern contains a != exclusion for each allowlisted ARN', () => {
    fc.assert(
      fc.property(allowlistArb, (allowlist) => {
        // Test all eventName-type rules
        const eventNameRules = DETECTION_RULES.filter((r) => r.type === 'eventName');
        for (const rule of eventNameRules) {
          const pattern = (construct as any).buildEventNameFilterPattern(
            rule.eventNames,
            allowlist
          );

          // Verify every allowlisted ARN appears as a != exclusion
          for (const arn of allowlist) {
            if (!pattern.includes(`$.userIdentity.arn != "${arn}"`)) {
              return false;
            }
          }
        }
        return true;
      }),
      { numRuns: 100 }
    );
  });

  it('the guardduty filter pattern contains a != exclusion for each allowlisted ARN', () => {
    fc.assert(
      fc.property(allowlistArb, (allowlist) => {
        // Test the guardduty rule specifically
        const guarddutyRule = DETECTION_RULES.find((r) => r.type === 'guardduty')!;
        const pattern = (construct as any).buildGuardDutyFilterPattern(
          guarddutyRule.eventNames,
          allowlist
        );

        // Verify every allowlisted ARN appears as a != exclusion
        for (const arn of allowlist) {
          if (!pattern.includes(`$.userIdentity.arn != "${arn}"`)) {
            return false;
          }
        }
        return true;
      }),
      { numRuns: 100 }
    );
  });

  it('all allowlist-aware filter patterns contain exclusions for every ARN in the allowlist', () => {
    fc.assert(
      fc.property(allowlistArb, (allowlist) => {
        // Collect all patterns from rules that use the allowlist (eventName + guardduty types)
        const patterns: string[] = [];

        for (const rule of DETECTION_RULES) {
          if (rule.type === 'eventName') {
            patterns.push(
              (construct as any).buildEventNameFilterPattern(rule.eventNames, allowlist)
            );
          } else if (rule.type === 'guardduty') {
            patterns.push(
              (construct as any).buildGuardDutyFilterPattern(rule.eventNames, allowlist)
            );
          }
          // stsRecon does not use the allowlist — excluded from this property
        }

        // Verify: every pattern must contain a != exclusion for every ARN
        for (const pattern of patterns) {
          for (const arn of allowlist) {
            if (!pattern.includes(`$.userIdentity.arn != "${arn}"`)) {
              return false;
            }
          }
        }
        return true;
      }),
      { numRuns: 100 }
    );
  });
});


// ---------------------------------------------------------------------------
// Feature: cloudtrail-detection, Property 4: Construct parameter validation
// **Validates: Requirements 7.1, 8.1, 8.4**
// ---------------------------------------------------------------------------

/**
 * Property 4: Construct parameter validation
 *
 * For any invalid construct parameter (empty cloudTrailLogGroupName, empty
 * serviceRoleArns array, serviceRoleArns with >10 entries, allowlistPrincipalArns
 * with >20 entries, empty alarmActionArn, or malformed ARN strings), the construct
 * SHALL throw a synthesis-time error with a message identifying the invalid parameter.
 *
 * **Validates: Requirements 7.1, 8.1, 8.4**
 */
describe('Property 4: Construct parameter validation', () => {
  // Valid base props to use as a starting point for generating invalid variants
  const validProps = {
    cloudTrailLogGroupName: '/aws/cloudtrail/test-trail',
    serviceRoleArns: ['arn:aws:iam::123456789012:role/test-role'],
    allowlistPrincipalArns: [],
    alarmActionArn: 'arn:aws:sns:us-east-1:123456789012:test-topic',
  };

  // Generator for valid IAM role ARNs (used to build oversized arrays)
  const validIamRoleArn = fc
    .tuple(
      fc.stringMatching(/^[1-9][0-9]{11}$/),
      fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9]{0,5}$/)
    )
    .map(([account, name]) => `arn:aws:iam::${account}:role/${name}`);

  // Generator for malformed ARNs (strings that do NOT start with 'arn:aws:')
  const malformedArn = fc.oneof(
    // Completely random strings that don't start with arn:aws:
    fc.string({ minLength: 1, maxLength: 50 }).filter((s) => !s.startsWith('arn:aws:')),
    // ARN-like but with wrong prefix
    fc.constantFrom(
      'arn:gcp:iam::123456789012:role/test',
      'arn:azure:iam::123456789012:role/test',
      'invalid-arn',
      'arn:aws',
      'arn:',
      'ARN:AWS:iam::123456789012:role/test'
    )
  );

  it('throws when cloudTrailLogGroupName is empty', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(''),
        (emptyName) => {
          const app = new cdk.App();
          const stack = new cdk.Stack(app, 'TestStack');
          expect(() => {
            new CloudTrailDetection(stack, 'TestDetection', {
              ...validProps,
              cloudTrailLogGroupName: emptyName,
            });
          }).toThrow(/cloudTrailLogGroupName/);
        }
      ),
      { numRuns: 1 }
    );
  });

  it('throws when cloudTrailLogGroupName exceeds 512 characters', () => {
    fc.assert(
      fc.property(
        // Generate strings of length 513–600
        fc.string({ minLength: 513, maxLength: 600 }),
        (longName) => {
          const app = new cdk.App();
          const stack = new cdk.Stack(app, 'TestStack');
          expect(() => {
            new CloudTrailDetection(stack, 'TestDetection', {
              ...validProps,
              cloudTrailLogGroupName: longName,
            });
          }).toThrow(/cloudTrailLogGroupName/);
        }
      ),
      { numRuns: 100 }
    );
  });

  it('throws when serviceRoleArns is empty', () => {
    fc.assert(
      fc.property(fc.constant([] as string[]), (emptyArns) => {
        const app = new cdk.App();
        const stack = new cdk.Stack(app, 'TestStack');
        expect(() => {
          new CloudTrailDetection(stack, 'TestDetection', {
            ...validProps,
            serviceRoleArns: emptyArns,
          });
        }).toThrow(/serviceRoleArns/);
      }),
      { numRuns: 1 }
    );
  });

  it('throws when serviceRoleArns has more than 10 entries', () => {
    fc.assert(
      fc.property(
        // Generate arrays of 11–15 valid ARNs
        fc.array(validIamRoleArn, { minLength: 11, maxLength: 15 }),
        (oversizedArns) => {
          const app = new cdk.App();
          const stack = new cdk.Stack(app, 'TestStack');
          expect(() => {
            new CloudTrailDetection(stack, 'TestDetection', {
              ...validProps,
              serviceRoleArns: oversizedArns,
            });
          }).toThrow(/serviceRoleArns/);
        }
      ),
      { numRuns: 100 }
    );
  });

  it('throws when allowlistPrincipalArns has more than 20 entries', () => {
    fc.assert(
      fc.property(
        // Generate arrays of 21–25 valid ARNs
        fc.array(validIamRoleArn, { minLength: 21, maxLength: 25 }),
        (oversizedAllowlist) => {
          const app = new cdk.App();
          const stack = new cdk.Stack(app, 'TestStack');
          expect(() => {
            new CloudTrailDetection(stack, 'TestDetection', {
              ...validProps,
              allowlistPrincipalArns: oversizedAllowlist,
            });
          }).toThrow(/allowlistPrincipalArns/);
        }
      ),
      { numRuns: 100 }
    );
  });

  it('throws when alarmActionArn is empty', () => {
    fc.assert(
      fc.property(fc.constantFrom(''), (emptyArn) => {
        const app = new cdk.App();
        const stack = new cdk.Stack(app, 'TestStack');
        expect(() => {
          new CloudTrailDetection(stack, 'TestDetection', {
            ...validProps,
            alarmActionArn: emptyArn,
          });
        }).toThrow(/alarmActionArn/);
      }),
      { numRuns: 1 }
    );
  });

  it('throws when serviceRoleArns contains a malformed ARN', () => {
    fc.assert(
      fc.property(malformedArn, (badArn) => {
        const app = new cdk.App();
        const stack = new cdk.Stack(app, 'TestStack');
        expect(() => {
          new CloudTrailDetection(stack, 'TestDetection', {
            ...validProps,
            serviceRoleArns: [badArn],
          });
        }).toThrow(/invalid ARN format in serviceRoleArns/);
      }),
      { numRuns: 100 }
    );
  });

  it('throws when allowlistPrincipalArns contains a malformed ARN', () => {
    fc.assert(
      fc.property(malformedArn, (badArn) => {
        const app = new cdk.App();
        const stack = new cdk.Stack(app, 'TestStack');
        expect(() => {
          new CloudTrailDetection(stack, 'TestDetection', {
            ...validProps,
            allowlistPrincipalArns: [badArn],
          });
        }).toThrow(/invalid ARN format in allowlistPrincipalArns/);
      }),
      { numRuns: 100 }
    );
  });

  it('throws when alarmActionArn is a malformed ARN (non-empty but invalid prefix)', () => {
    fc.assert(
      fc.property(
        // Generate non-empty strings that don't start with 'arn:aws:'
        fc.string({ minLength: 1, maxLength: 50 }).filter(
          (s) => s.length > 0 && !s.startsWith('arn:aws:')
        ),
        (badArn) => {
          const app = new cdk.App();
          const stack = new cdk.Stack(app, 'TestStack');
          expect(() => {
            new CloudTrailDetection(stack, 'TestDetection', {
              ...validProps,
              alarmActionArn: badArn,
            });
          }).toThrow(/alarmActionArn/);
        }
      ),
      { numRuns: 100 }
    );
  });
});


// ---------------------------------------------------------------------------
// Feature: cloudtrail-detection, Property 1: Metric filter pattern correctness for eventName-based rules
// **Validates: Requirements 1.1, 2.1, 3.1, 4.1, 5.1, 7.2**
// ---------------------------------------------------------------------------

/**
 * All eventName-based detection rules (type === 'eventName').
 * These are the rules whose filter patterns are built by buildEventNameFilterPattern.
 */
const EVENT_NAME_RULES = DETECTION_RULES.filter((rule) => rule.type === 'eventName');

/**
 * All possible eventNames across all eventName-based rules.
 */
const ALL_EVENT_NAMES = EVENT_NAME_RULES.flatMap((rule) => rule.eventNames);

/**
 * Generator for valid IAM role ARNs suitable for the allowlist.
 * Produces ARNs like: arn:aws:iam::123456789012:role/some-role-name
 * Keeps role names short to avoid exceeding the 1024-char pattern limit.
 */
const iamRoleArn = fc
  .tuple(
    fc.stringMatching(/^[1-9][0-9]{11}$/),
    fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9-]{0,20}$/)
  )
  .map(([account, name]) => `arn:aws:iam::${account}:role/${name}`);

/**
 * Property 1: Metric filter pattern correctness for eventName-based rules
 *
 * For any valid detection rule (cloudtrail-tampering, iam-privilege-escalation,
 * vpc-network-changes, security-group-changes) and for any valid allowlist of
 * principal ARNs, the synthesized metric filter pattern SHALL contain every
 * target eventName as an equality match AND shall contain a != exclusion clause
 * for every allowlisted principal ARN.
 *
 * **Validates: Requirements 1.1, 2.1, 3.1, 4.1, 5.1, 7.2**
 */
describe('Property 1: Metric filter pattern correctness for eventName-based rules', () => {
  let construct: CloudTrailDetection;

  beforeEach(() => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'TestStack');
    construct = new CloudTrailDetection(stack, 'TestDetection', {
      cloudTrailLogGroupName: '/aws/cloudtrail/test-trail',
      serviceRoleArns: ['arn:aws:iam::123456789012:role/test-role'],
      allowlistPrincipalArns: [],
      alarmActionArn: 'arn:aws:sns:us-east-1:123456789012:test-topic',
    });
  });

  it('filter pattern contains every target eventName as an equality match for any random subset of eventNames', () => {
    fc.assert(
      fc.property(
        // Generate a random non-empty subset of eventNames from the eventName-based rules.
        // Limit to 10 to stay within the 1024-char pattern limit when combined with allowlist ARNs.
        fc.subarray(ALL_EVENT_NAMES, { minLength: 1, maxLength: 10 }),
        // Generate a random allowlist of 0–5 valid IAM role ARNs (kept small to stay under 1024 chars)
        fc.array(iamRoleArn, { minLength: 0, maxLength: 5 }),
        (eventNames, allowlist) => {
          const pattern = (construct as any).buildEventNameFilterPattern(
            eventNames,
            allowlist
          );

          // Verify every eventName appears as an equality match in the pattern
          for (const eventName of eventNames) {
            expect(pattern).toContain(`$.eventName = "${eventName}"`);
          }
        }
      ),
      { numRuns: 100 }
    );
  });

  it('filter pattern contains a != exclusion for every allowlisted principal ARN', () => {
    fc.assert(
      fc.property(
        // Generate a random non-empty subset of eventNames (kept small to leave room for allowlist)
        fc.subarray(ALL_EVENT_NAMES, { minLength: 1, maxLength: 3 }),
        // Generate a random allowlist of 1–5 valid IAM role ARNs
        fc.array(iamRoleArn, { minLength: 1, maxLength: 5 }),
        (eventNames, allowlist) => {
          const pattern = (construct as any).buildEventNameFilterPattern(
            eventNames,
            allowlist
          );

          // Verify every allowlisted ARN appears as a != exclusion in the pattern
          for (const arn of allowlist) {
            expect(pattern).toContain(`$.userIdentity.arn != "${arn}"`);
          }
        }
      ),
      { numRuns: 100 }
    );
  });

  it('filter pattern with empty allowlist contains no exclusion clauses', () => {
    fc.assert(
      fc.property(
        // Generate a random non-empty subset of eventNames
        fc.subarray(ALL_EVENT_NAMES, { minLength: 1 }),
        (eventNames) => {
          const pattern = (construct as any).buildEventNameFilterPattern(
            eventNames,
            []
          );

          // Verify no exclusion clauses are present
          expect(pattern).not.toContain('$.userIdentity.arn !=');

          // Verify all eventNames are still present
          for (const eventName of eventNames) {
            expect(pattern).toContain(`$.eventName = "${eventName}"`);
          }
        }
      ),
      { numRuns: 100 }
    );
  });
});


// ---------------------------------------------------------------------------
// Feature: cloudtrail-detection, Property 3: Resource count invariant
// **Validates: Requirements 8.2, 9.1**
// ---------------------------------------------------------------------------

/**
 * Property 3: Resource count invariant
 *
 * For any valid set of construct parameters (valid log group name, 1–10 service
 * role ARNs, 0–20 allowlist ARNs, valid alarm action ARN), the synthesized
 * CloudFormation template SHALL contain exactly 6 AWS::Logs::MetricFilter
 * resources, exactly 6 AWS::CloudWatch::Alarm resources, and all alarms SHALL
 * reference the provided alarm action ARN.
 *
 * **Validates: Requirements 8.2, 9.1**
 */
describe('Property 3: Resource count invariant', () => {
  // Generator for valid log group names (starts with '/')
  const logGroupName = fc
    .stringMatching(/^[a-z][a-z0-9\-]{1,30}$/)
    .map((s: string) => `/aws/cloudtrail/${s}`);

  // Generator for valid IAM role ARNs with short names (1-4 chars) to stay
  // under the 1024-char CloudWatch Metric Filter pattern limit
  const iamRoleArnShort = fc
    .tuple(
      fc.stringMatching(/^[1-9][0-9]{11}$/),
      fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9]{0,3}$/)
    )
    .map(([account, name]: [string, string]) => `arn:aws:iam::${account}:role/${name}`);

  // Generator for valid SNS topic ARNs
  const snsTopicArn = fc
    .tuple(
      fc.constantFrom('us-east-1', 'us-west-2', 'eu-west-1'),
      fc.stringMatching(/^[1-9][0-9]{11}$/),
      fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9]{0,10}$/)
    )
    .map(([region, account, name]: [string, string, string]) => `arn:aws:sns:${region}:${account}:${name}`);

  // Generator for valid construct props with varying sizes
  const validPropsArb = fc.tuple(
    logGroupName,
    fc.array(iamRoleArnShort, { minLength: 1, maxLength: 10 }),
    fc.array(iamRoleArnShort, { minLength: 0, maxLength: 5 }),
    snsTopicArn
  );

  it('synthesized template contains exactly 6 MetricFilter and 6 Alarm resources', () => {
    fc.assert(
      fc.property(validPropsArb, (tuple) => {
        const [lgName, serviceRoles, allowlist, alarmArn] = tuple;
        const app = new cdk.App();
        const stack = new cdk.Stack(app, 'TestStack');

        new CloudTrailDetection(stack, 'Detection', {
          cloudTrailLogGroupName: lgName,
          serviceRoleArns: serviceRoles,
          allowlistPrincipalArns: allowlist,
          alarmActionArn: alarmArn,
        });

        const template = Template.fromStack(stack);

        // Verify exactly 6 metric filters
        template.resourceCountIs('AWS::Logs::MetricFilter', 6);

        // Verify exactly 6 alarms
        template.resourceCountIs('AWS::CloudWatch::Alarm', 6);
      }),
      { numRuns: 100 }
    );
  });

  it('all alarms reference the provided alarm action ARN', () => {
    fc.assert(
      fc.property(validPropsArb, (tuple) => {
        const [lgName, serviceRoles, allowlist, alarmArn] = tuple;
        const app = new cdk.App();
        const stack = new cdk.Stack(app, 'TestStack');

        new CloudTrailDetection(stack, 'Detection', {
          cloudTrailLogGroupName: lgName,
          serviceRoleArns: serviceRoles,
          allowlistPrincipalArns: allowlist,
          alarmActionArn: alarmArn,
        });

        const template = Template.fromStack(stack);

        // Get all alarm resources from the template
        const alarms = template.findResources('AWS::CloudWatch::Alarm');
        const alarmKeys = Object.keys(alarms);

        // Verify we have exactly 6 alarms
        expect(alarmKeys).toHaveLength(6);

        // Verify each alarm has AlarmActions referencing the provided ARN
        // The SNS topic is imported via Topic.fromTopicArn, so the alarm action
        // will reference the ARN directly
        for (const key of alarmKeys) {
          const alarmResource = alarms[key];
          const alarmActions = alarmResource.Properties.AlarmActions;
          expect(alarmActions).toBeDefined();
          expect(alarmActions.length).toBeGreaterThanOrEqual(1);

          // At least one alarm action should reference the provided ARN
          const hasCorrectArn = alarmActions.some(
            (action: any) => action === alarmArn || JSON.stringify(action).includes(alarmArn)
          );
          expect(hasCorrectArn).toBe(true);
        }
      }),
      { numRuns: 100 }
    );
  });
});


// ---------------------------------------------------------------------------
// Feature: cloudtrail-detection, Property 5: Alarm configuration consistency
// **Validates: Requirements 1.2, 1.3, 2.2, 2.3, 2.4, 3.3, 3.4, 4.2, 4.3, 5.2, 5.3, 6.2, 6.3**
// ---------------------------------------------------------------------------

/**
 * Property 5: Alarm configuration consistency
 *
 * For any valid construct parameters, all 6 synthesized alarms SHALL have
 * period=300, evaluationPeriods=1, threshold=1,
 * comparisonOperator=GreaterThanOrEqualToThreshold, statistic=Sum, and
 * treatMissingData=notBreaching. Additionally, exactly 5 alarms SHALL have
 * "HIGH" in their description and exactly 1 alarm SHALL have "MEDIUM" in
 * its description.
 *
 * **Validates: Requirements 1.2, 1.3, 2.2, 2.3, 2.4, 3.3, 3.4, 4.2, 4.3, 5.2, 5.3, 6.2, 6.3**
 */
describe('Property 5: Alarm configuration consistency', () => {
  // Generator for valid log group names (starts with '/')
  const logGroupNameArb = fc
    .stringMatching(/^[a-z][a-z0-9\-]{1,30}$/)
    .map((s: string) => `/aws/cloudtrail/${s}`);

  // Generator for valid IAM role ARNs with short names (1-4 chars) to stay
  // under the 1024-char CloudWatch Metric Filter pattern limit
  const shortIamRoleArn = fc
    .tuple(
      fc.stringMatching(/^[1-9][0-9]{11}$/),
      fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9]{0,3}$/)
    )
    .map(([account, name]: [string, string]) => `arn:aws:iam::${account}:role/${name}`);

  // Generator for valid SNS topic ARNs
  const snsTopicArnArb = fc
    .tuple(
      fc.constantFrom('us-east-1', 'us-west-2', 'eu-west-1'),
      fc.stringMatching(/^[1-9][0-9]{11}$/),
      fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9]{0,10}$/)
    )
    .map(([region, account, name]: [string, string, string]) => `arn:aws:sns:${region}:${account}:${name}`);

  // Generator for valid construct props with varying sizes
  const validPropsArb = fc.tuple(
    logGroupNameArb,
    fc.array(shortIamRoleArn, { minLength: 1, maxLength: 5 }),
    fc.array(shortIamRoleArn, { minLength: 0, maxLength: 5 }),
    snsTopicArnArb
  );

  it('all 6 alarms have period=300, evaluationPeriods=1, threshold=1, comparisonOperator=GreaterThanOrEqualToThreshold, statistic=Sum, treatMissingData=notBreaching', () => {
    fc.assert(
      fc.property(validPropsArb, ([lgName, serviceRoles, allowlist, alarmArn]) => {
        const app = new cdk.App();
        const stack = new cdk.Stack(app, 'TestStack');
        new CloudTrailDetection(stack, 'TestDetection', {
          cloudTrailLogGroupName: lgName,
          serviceRoleArns: serviceRoles,
          allowlistPrincipalArns: allowlist,
          alarmActionArn: alarmArn,
        });

        const template = Template.fromStack(stack);
        const alarms = template.findResources('AWS::CloudWatch::Alarm');
        const alarmEntries = Object.values(alarms);

        // Verify exactly 6 alarms
        expect(alarmEntries).toHaveLength(6);

        // Verify each alarm has the correct configuration
        for (const alarm of alarmEntries) {
          const alarmProps = (alarm as any).Properties;

          expect(alarmProps.Period).toBe(300);
          expect(alarmProps.EvaluationPeriods).toBe(1);
          expect(alarmProps.Threshold).toBe(1);
          expect(alarmProps.ComparisonOperator).toBe('GreaterThanOrEqualToThreshold');
          expect(alarmProps.Statistic).toBe('Sum');
          expect(alarmProps.TreatMissingData).toBe('notBreaching');
        }
      }),
      { numRuns: 100 }
    );
  });

  it('exactly 5 alarms have "HIGH" in description and 1 has "MEDIUM"', () => {
    fc.assert(
      fc.property(validPropsArb, ([lgName, serviceRoles, allowlist, alarmArn]) => {
        const app = new cdk.App();
        const stack = new cdk.Stack(app, 'TestStack');
        new CloudTrailDetection(stack, 'TestDetection', {
          cloudTrailLogGroupName: lgName,
          serviceRoleArns: serviceRoles,
          allowlistPrincipalArns: allowlist,
          alarmActionArn: alarmArn,
        });

        const template = Template.fromStack(stack);
        const alarms = template.findResources('AWS::CloudWatch::Alarm');
        const alarmEntries = Object.values(alarms);

        // Count alarms by severity in description
        let highCount = 0;
        let mediumCount = 0;

        for (const alarm of alarmEntries) {
          const description: string = (alarm as any).Properties.AlarmDescription;
          if (description.includes('[HIGH]')) {
            highCount++;
          } else if (description.includes('[MEDIUM]')) {
            mediumCount++;
          }
        }

        expect(highCount).toBe(5);
        expect(mediumCount).toBe(1);
      }),
      { numRuns: 100 }
    );
  });
});


// ---------------------------------------------------------------------------
// Task 5.2: Unit tests for stack integration
// **Validates: Requirements 8.2, 8.3, 9.2**
// ---------------------------------------------------------------------------

/**
 * Integration tests that verify the CloudTrailDetection construct works
 * correctly when instantiated within a stack that mirrors the real
 * CknIngestionStack (with 4 service role ARNs).
 */
describe('CloudTrailDetection - Stack Integration', () => {
  // Realistic props that mirror what CknIngestionStack passes
  const integrationProps = {
    cloudTrailLogGroupName: '/aws/cloudtrail/ckn-trail',
    serviceRoleArns: [
      'arn:aws:iam::123456789012:role/ckn-ingestion-task-role',
      'arn:aws:iam::123456789012:role/ckn-ingestion-execution-role',
      'arn:aws:iam::123456789012:role/ckn-kb-role',
      'arn:aws:iam::123456789012:role/ckn-event-role',
    ],
    allowlistPrincipalArns: [] as string[],
    alarmActionArn: 'arn:aws:sns:us-east-1:123456789012:ckn-security-alerts',
  };

  it('synthesizes without error within a standalone stack (mock CknIngestionStack)', () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'MockCknIngestionStack', {
      env: { account: '123456789012', region: 'us-east-1' },
    });

    // Should not throw
    new CloudTrailDetection(stack, 'CloudTrailDetection', integrationProps);

    // Verify synthesis completes without error
    const template = Template.fromStack(stack);
    expect(template).toBeDefined();
  });

  it('produces valid CloudFormation output with all 12 resources (6 filters + 6 alarms)', () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'MockCknIngestionStack');

    new CloudTrailDetection(stack, 'CloudTrailDetection', integrationProps);

    const template = Template.fromStack(stack);

    // Verify exactly 6 metric filters
    template.resourceCountIs('AWS::Logs::MetricFilter', 6);

    // Verify exactly 6 alarms
    template.resourceCountIs('AWS::CloudWatch::Alarm', 6);
  });

  it('STS recon filter pattern uses wildcard matching for the 4 service role names', () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'MockCknIngestionStack');

    new CloudTrailDetection(stack, 'CloudTrailDetection', integrationProps);

    const template = Template.fromStack(stack);
    const metricFilters = template.findResources('AWS::Logs::MetricFilter');

    // Find the sts-recon metric filter by looking for the one with
    // eventSource = "sts.amazonaws.com" in its filter pattern
    const stsReconFilter = Object.values(metricFilters).find((resource: any) => {
      const pattern: string = resource.Properties.FilterPattern;
      return pattern.includes('sts.amazonaws.com');
    });

    expect(stsReconFilter).toBeDefined();
    const filterPattern: string = (stsReconFilter as any).Properties.FilterPattern;

    // Verify it uses wildcard matching for each of the 4 role names
    expect(filterPattern).toContain('*ckn-ingestion-task-role*');
    expect(filterPattern).toContain('*ckn-ingestion-execution-role*');
    expect(filterPattern).toContain('*ckn-kb-role*');
    expect(filterPattern).toContain('*ckn-event-role*');

    // Verify the pattern matches GetCallerIdentity from sts.amazonaws.com
    expect(filterPattern).toContain('$.eventSource = "sts.amazonaws.com"');
    expect(filterPattern).toContain('$.eventName = "GetCallerIdentity"');

    // Verify wildcard matching uses the $.userIdentity.arn field
    expect(filterPattern).toContain('$.userIdentity.arn = "*ckn-ingestion-task-role*"');
    expect(filterPattern).toContain('$.userIdentity.arn = "*ckn-ingestion-execution-role*"');
    expect(filterPattern).toContain('$.userIdentity.arn = "*ckn-kb-role*"');
    expect(filterPattern).toContain('$.userIdentity.arn = "*ckn-event-role*"');
  });

  it('GuardDuty rule includes the requestParameters.enable IS FALSE condition', () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'MockCknIngestionStack');

    new CloudTrailDetection(stack, 'CloudTrailDetection', integrationProps);

    const template = Template.fromStack(stack);
    const metricFilters = template.findResources('AWS::Logs::MetricFilter');

    // Find the guardduty-disabling metric filter by looking for UpdateDetector
    const guarddutyFilter = Object.values(metricFilters).find((resource: any) => {
      const pattern: string = resource.Properties.FilterPattern;
      return pattern.includes('UpdateDetector') && pattern.includes('DeleteDetector');
    });

    expect(guarddutyFilter).toBeDefined();
    const filterPattern: string = (guarddutyFilter as any).Properties.FilterPattern;

    // Verify the requestParameters.enable IS FALSE condition is present
    expect(filterPattern).toContain('$.requestParameters.enable IS FALSE');

    // Verify UpdateDetector is paired with the enable IS FALSE condition
    expect(filterPattern).toContain('$.eventName = "UpdateDetector" && $.requestParameters.enable IS FALSE');

    // Verify other GuardDuty events are also present
    expect(filterPattern).toContain('$.eventName = "DeleteDetector"');
    expect(filterPattern).toContain('$.eventName = "StopMonitoringMembers"');
    expect(filterPattern).toContain('$.eventName = "DisassociateMembers"');
  });

  it('alarm descriptions contain the rule name and severity', () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'MockCknIngestionStack');

    new CloudTrailDetection(stack, 'CloudTrailDetection', integrationProps);

    const template = Template.fromStack(stack);
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const alarmEntries = Object.values(alarms);

    // Expected rule IDs and their severities
    const expectedRules = [
      { id: 'cloudtrail-tampering', severity: 'HIGH' },
      { id: 'iam-privilege-escalation', severity: 'HIGH' },
      { id: 'guardduty-disabling', severity: 'HIGH' },
      { id: 'vpc-network-changes', severity: 'HIGH' },
      { id: 'security-group-changes', severity: 'MEDIUM' },
      { id: 'sts-recon', severity: 'HIGH' },
    ];

    // Collect all alarm descriptions
    const descriptions = alarmEntries.map(
      (alarm: any) => alarm.Properties.AlarmDescription as string
    );

    // Verify each expected rule has a matching alarm description
    for (const rule of expectedRules) {
      const matchingDescription = descriptions.find(
        (desc) => desc.includes(rule.id) && desc.includes(`[${rule.severity}]`)
      );
      expect(matchingDescription).toBeDefined();
    }
  });

  it('works with allowlisted principals (exclusion clauses in eventName-based filters)', () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'MockCknIngestionStack');

    const propsWithAllowlist = {
      ...integrationProps,
      allowlistPrincipalArns: [
        'arn:aws:iam::123456789012:role/cdk-cfn-exec-role',
        'arn:aws:iam::123456789012:role/cdk-deploy-role',
      ],
    };

    new CloudTrailDetection(stack, 'CloudTrailDetection', propsWithAllowlist);

    const template = Template.fromStack(stack);
    const metricFilters = template.findResources('AWS::Logs::MetricFilter');

    // Find the cloudtrail-tampering filter (has DeleteTrail)
    const tamperingFilter = Object.values(metricFilters).find((resource: any) => {
      const pattern: string = resource.Properties.FilterPattern;
      return pattern.includes('DeleteTrail') && pattern.includes('StopLogging');
    });

    expect(tamperingFilter).toBeDefined();
    const filterPattern: string = (tamperingFilter as any).Properties.FilterPattern;

    // Verify allowlist exclusion clauses are present
    expect(filterPattern).toContain('$.userIdentity.arn != "arn:aws:iam::123456789012:role/cdk-cfn-exec-role"');
    expect(filterPattern).toContain('$.userIdentity.arn != "arn:aws:iam::123456789012:role/cdk-deploy-role"');
  });

  it('all alarms reference the provided alarm action ARN', () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'MockCknIngestionStack');

    new CloudTrailDetection(stack, 'CloudTrailDetection', integrationProps);

    const template = Template.fromStack(stack);
    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    const alarmEntries = Object.values(alarms);

    expect(alarmEntries).toHaveLength(6);

    for (const alarm of alarmEntries) {
      const alarmActions = (alarm as any).Properties.AlarmActions;
      expect(alarmActions).toBeDefined();
      expect(alarmActions.length).toBeGreaterThanOrEqual(1);

      // The alarm action should reference the SNS topic ARN
      const hasCorrectArn = alarmActions.some(
        (action: any) =>
          action === integrationProps.alarmActionArn ||
          JSON.stringify(action).includes(integrationProps.alarmActionArn)
      );
      expect(hasCorrectArn).toBe(true);
    }
  });
});

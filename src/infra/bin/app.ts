#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { CknIngestionStack } from '../lib/ckn-ingestion-stack';

const app = new cdk.App();
new CknIngestionStack(app, 'CknIngestionStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});

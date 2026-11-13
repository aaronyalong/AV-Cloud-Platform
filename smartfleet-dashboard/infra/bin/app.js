#!/usr/bin/env node
const cdk = require('aws-cdk-lib');
const { DashboardStack } = require('../lib/dashboard-stack');

const app = new cdk.App();
new DashboardStack(app, 'SmartFleetDashboard', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION || 'us-west-2' },
  description: 'CMPE 281 SmartFleet - System Dashboard component',
});

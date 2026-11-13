// SmartFleet System Dashboard - AWS infrastructure (CDK v2, JavaScript)
// CloudFront + S3 (React SPA) | /api/* -> ALB -> ECS Fargate (FastAPI x2) | worker (Fargate x1)
// RDS PostgreSQL 16 | Amazon DocumentDB | S3 media | Cognito user pool + groups | Secrets Manager
const path = require('path');
const cdk = require('aws-cdk-lib');
const ec2 = require('aws-cdk-lib/aws-ec2');
const ecs = require('aws-cdk-lib/aws-ecs');
const ecsPatterns = require('aws-cdk-lib/aws-ecs-patterns');
const ecrAssets = require('aws-cdk-lib/aws-ecr-assets');
const rds = require('aws-cdk-lib/aws-rds');
const docdb = require('aws-cdk-lib/aws-docdb');
const s3 = require('aws-cdk-lib/aws-s3');
const s3deploy = require('aws-cdk-lib/aws-s3-deployment');
const cloudfront = require('aws-cdk-lib/aws-cloudfront');
const origins = require('aws-cdk-lib/aws-cloudfront-origins');
const cognito = require('aws-cdk-lib/aws-cognito');
const secrets = require('aws-cdk-lib/aws-secretsmanager');
const logs = require('aws-cdk-lib/aws-logs');

const ROLES = ['platform_admin', 'tenant_admin', 'fleet_operator', 'support_staff', 'vehicle_owner', 'ai_engineer'];

class DashboardStack extends cdk.Stack {
  constructor(scope, id, props) {
    super(scope, id, props);
    const destroy = cdk.RemovalPolicy.DESTROY; // demo stack: everything is deleted by `cdk destroy`

    // ---------- Network: 2 AZs, public subnets (Fargate with public IPs, no NAT cost) + isolated (databases)
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2, natGateways: 0,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'data', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
    });
    const appSg = new ec2.SecurityGroup(this, 'AppSg', { vpc, description: 'API and worker tasks' });

    // ---------- RDS PostgreSQL (alerts, vehicles, rollups, models)
    const db = new rds.DatabaseInstance(this, 'Postgres', {
      engine: rds.DatabaseInstanceEngine.postgres({ version: rds.PostgresEngineVersion.VER_16 }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
      vpc, vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      credentials: rds.Credentials.fromGeneratedSecret('dash'),
      databaseName: 'smartfleet', allocatedStorage: 20, storageEncrypted: true, multiAz: false, publiclyAccessible: false,
      backupRetention: cdk.Duration.days(1), deletionProtection: false, removalPolicy: destroy,
    });
    db.connections.allowDefaultPortFrom(appSg);

    // ---------- Amazon DocumentDB (raw telemetry buckets)
    const docCluster = new docdb.DatabaseCluster(this, 'DocDb', {
      masterUser: { username: 'dbadmin' },
      engineVersion: '5.0.0',
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM),
      instances: 1, vpc, vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      removalPolicy: destroy,
    });
    docCluster.connections.allowDefaultPortFrom(appSg);

    // ---------- S3: media/evidence/exports/model artifacts (private; presigned URLs only)
    const media = new s3.Bucket(this, 'Media', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL, encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true, removalPolicy: destroy, autoDeleteObjects: true,
      cors: [{ allowedMethods: [s3.HttpMethods.GET, s3.HttpMethods.PUT, s3.HttpMethods.HEAD],
               allowedOrigins: ['*'], allowedHeaders: ['*'], maxAge: 3000 }],
    });

    // ---------- Simulator key (shared secret for the alert-module stub endpoints)
    const simKey = new secrets.Secret(this, 'SimKey', {
      generateSecretString: { excludePunctuation: true, passwordLength: 32 },
    });

    // ---------- Cognito user pool, role groups, custom:tenant_id
    const userPool = new cognito.UserPool(this, 'Users', {
      userPoolName: 'smartfleet-users', selfSignUpEnabled: false, signInAliases: { email: true },
      standardAttributes: { email: { required: true, mutable: true }, fullname: { required: false, mutable: true } },
      customAttributes: { tenant_id: new cognito.StringAttribute({ mutable: true, minLen: 1, maxLen: 16 }) },
      passwordPolicy: { minLength: 8, requireSymbols: false },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY, removalPolicy: destroy,
    });
    ROLES.forEach((r) => new cognito.CfnUserPoolGroup(this, `Group-${r}`, { userPoolId: userPool.userPoolId, groupName: r }));
    const domain = userPool.addDomain('Domain', { cognitoDomain: { domainPrefix: `smartfleet-${cdk.Aws.ACCOUNT_ID}` } });

    // ---------- Container image (built from ../backend on your Mac; ARM64 = Graviton Fargate)
    const image = ecs.ContainerImage.fromAsset(path.join(__dirname, '../../backend'), {
      platform: ecrAssets.Platform.LINUX_ARM64,
    });
    const cluster = new ecs.Cluster(this, 'Cluster', { vpc, containerInsightsV2: ecs.ContainerInsights.DISABLED });

    // ---------- API service: ALB + 2 Fargate tasks
    const api = new ecsPatterns.ApplicationLoadBalancedFargateService(this, 'Api', {
      cluster, cpu: 512, memoryLimitMiB: 1024, desiredCount: 2,
      runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.ARM64, operatingSystemFamily: ecs.OperatingSystemFamily.LINUX },
      publicLoadBalancer: true, assignPublicIp: true, taskSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      securityGroups: [appSg], healthCheckGracePeriod: cdk.Duration.seconds(120), minHealthyPercent: 50,
      circuitBreaker: { rollback: true },
      taskImageOptions: {
        image, containerPort: 8000,
        logDriver: ecs.LogDrivers.awsLogs({ streamPrefix: 'api', logRetention: logs.RetentionDays.ONE_WEEK }),
        environment: {}, secrets: {},
      },
    });
    api.targetGroup.configureHealthCheck({ path: '/healthz', healthyHttpCodes: '200', interval: cdk.Duration.seconds(15) });
    api.targetGroup.setAttribute('deregistration_delay.timeout_seconds', '30');

    // ---------- CloudFront: SPA from S3 + /api/* to the ALB on one HTTPS origin (no CORS, no custom cert)
    const site = new s3.Bucket(this, 'Site', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL, enforceSSL: true, removalPolicy: destroy, autoDeleteObjects: true,
    });
    const spaRewrite = new cloudfront.Function(this, 'SpaRewrite', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        "function handler(event){var r=event.request;if(r.uri.indexOf('.')===-1){r.uri='/index.html';}return r;}"),
    });
    const dist = new cloudfront.Distribution(this, 'Cdn', {
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(site),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        functionAssociations: [{ function: spaRewrite, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.HttpOrigin(api.loadBalancer.loadBalancerDnsName, {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY, readTimeout: cdk.Duration.seconds(30),
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
      },
    });
    const siteUrl = `https://${dist.distributionDomainName}`;

    const client = userPool.addClient('WebClient', {
      generateSecret: false,
      authFlows: { userSrp: true, adminUserPassword: true },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: [`${siteUrl}/callback`, 'http://localhost:5173/callback'],
        logoutUrls: [`${siteUrl}/`, 'http://localhost:5173/'],
      },
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
      idTokenValidity: cdk.Duration.hours(1), refreshTokenValidity: cdk.Duration.days(1),
    });

    // ---------- Shared container settings for API and worker
    const environment = {
      APP_ENV: 'aws', AUTH_MODE: 'cognito', AWS_REGION: this.region, DEFAULT_TZ: 'America/Los_Angeles',
      COGNITO_USER_POOL_ID: userPool.userPoolId, COGNITO_CLIENT_ID: client.userPoolClientId,
      DB_HOST: db.instanceEndpoint.hostname, DB_PORT: '5432', DB_NAME: 'smartfleet', DB_USER: 'dash', DB_SSLMODE: 'require',
      DOCDB_HOST: docCluster.clusterEndpoint.hostname, S3_BUCKET: media.bucketName,
    };
    const secretsEnv = {
      DB_PASSWORD: ecs.Secret.fromSecretsManager(db.secret, 'password'),
      DOCDB_USER: ecs.Secret.fromSecretsManager(docCluster.secret, 'username'),
      DOCDB_PASSWORD: ecs.Secret.fromSecretsManager(docCluster.secret, 'password'),
      SIM_API_KEY: ecs.Secret.fromSecretsManager(simKey),
    };
    const apiContainer = api.taskDefinition.defaultContainer;
    Object.entries(environment).forEach(([k, v]) => apiContainer.addEnvironment(k, v));
    Object.entries(secretsEnv).forEach(([k, v]) => apiContainer.addSecret(k, v));
    media.grantReadWrite(api.taskDefinition.taskRole);

    // ---------- Stats rollup worker (same image, different command)
    const workerTd = new ecs.FargateTaskDefinition(this, 'WorkerTask', {
      cpu: 256, memoryLimitMiB: 512,
      runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.ARM64, operatingSystemFamily: ecs.OperatingSystemFamily.LINUX },
    });
    workerTd.addContainer('worker', {
      image, command: ['python', '-m', 'app.worker'], environment, secrets: secretsEnv,
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'worker', logRetention: logs.RetentionDays.ONE_WEEK }),
    });
    media.grantReadWrite(workerTd.taskRole);
    new ecs.FargateService(this, 'Worker', {
      cluster, taskDefinition: workerTd, desiredCount: 1, assignPublicIp: true,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC }, securityGroups: [appSg], minHealthyPercent: 0,
      circuitBreaker: { rollback: true },
    });

    // ---------- Upload the built SPA + runtime config.json
    new s3deploy.BucketDeployment(this, 'DeploySite', {
      sources: [
        s3deploy.Source.asset(path.join(__dirname, '../../frontend/dist'), { exclude: ['config.json'] }),
        s3deploy.Source.jsonData('config.json', {
          authMode: 'cognito', region: this.region, userPoolId: userPool.userPoolId, clientId: client.userPoolClientId,
          cognitoDomain: `${domain.domainName}.auth.${this.region}.amazoncognito.com`,
        }),
      ],
      destinationBucket: site, distribution: dist, distributionPaths: ['/*'], memoryLimit: 512,
    });

    // ---------- Outputs used by the guide's scripts
    new cdk.CfnOutput(this, 'DashboardUrl', { value: siteUrl });
    new cdk.CfnOutput(this, 'ApiDocsUrl', { value: `${siteUrl}/api/docs` });
    new cdk.CfnOutput(this, 'UserPoolId', { value: userPool.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', { value: client.userPoolClientId });
    new cdk.CfnOutput(this, 'CognitoDomain', { value: `${domain.domainName}.auth.${this.region}.amazoncognito.com` });
    new cdk.CfnOutput(this, 'SimKeySecretArn', { value: simKey.secretArn });
    new cdk.CfnOutput(this, 'MediaBucket', { value: media.bucketName });
    new cdk.CfnOutput(this, 'ClusterName', { value: cluster.clusterName });
  }
}

module.exports = { DashboardStack };

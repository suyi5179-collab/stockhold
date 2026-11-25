// The whole stack as CDK. `npm run deploy:local` deploys it to LocalStack through cdklocal;
// CI runs `cdk synth` so a template that cannot synthesise fails the build.
import * as cdk from 'aws-cdk-lib'
import * as ddb from 'aws-cdk-lib/aws-dynamodb'
import * as events from 'aws-cdk-lib/aws-events'
import * as targets from 'aws-cdk-lib/aws-events-targets'
import * as lambda from 'aws-cdk-lib/aws-lambda'
import { DynamoEventSource, SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources'
import * as s3 from 'aws-cdk-lib/aws-s3'
import * as sqs from 'aws-cdk-lib/aws-sqs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const app = new cdk.App()
const stack = new cdk.Stack(app, 'Stockhold', { env: { account: '000000000000', region: 'us-east-1' } })

// @learn:cdk-table
const table = new ddb.Table(stack, 'Table', {
  tableName: 'stockhold',
  partitionKey: { name: 'PK', type: ddb.AttributeType.STRING },
  sortKey: { name: 'SK', type: ddb.AttributeType.STRING },
  billingMode: ddb.BillingMode.PAY_PER_REQUEST,
  stream: ddb.StreamViewType.NEW_AND_OLD_IMAGES,   // feeds the event log (chapter 07)
  removalPolicy: cdk.RemovalPolicy.DESTROY,
})
// Sparse: only HELD holds carry gsi1pk, so the sweeper reads open holds and nothing else.
table.addGlobalSecondaryIndex({
  indexName: 'gsi1',
  partitionKey: { name: 'gsi1pk', type: ddb.AttributeType.STRING },
  sortKey: { name: 'gsi1sk', type: ddb.AttributeType.NUMBER },
})
// Holds by product, keyed per stock shard so a flash-sale product is not one hot index partition.
table.addGlobalSecondaryIndex({
  indexName: 'gsi2',
  partitionKey: { name: 'gsi2pk', type: ddb.AttributeType.STRING },
  sortKey: { name: 'gsi2sk', type: ddb.AttributeType.NUMBER },
})
// @learn:end cdk-table

const code = lambda.Code.fromAsset(path.join(here, '../build/lambda'))
const env = {
  STOCKHOLD_TABLE: table.tableName,
  STOCKHOLD_EXPIRY_QUEUE: 'stockhold-hold-expiry',
  STOCKHOLD_PAYMENTS_QUEUE: 'stockhold-payment-events',
}
const fn = (id, handler, extra = {}, timeoutS = 30) => {
  const f = new lambda.Function(stack, id, {
    functionName: `stockhold-${id.toLowerCase()}`, runtime: lambda.Runtime.PYTHON_3_11, handler, code,
    memorySize: 256, timeout: cdk.Duration.seconds(timeoutS), environment: { ...env, ...extra },
  })
  table.grantReadWriteData(f)
  return f
}

// @learn:cdk-expiry
const expiryDlq = new sqs.Queue(stack, 'ExpiryDlq', { queueName: 'stockhold-hold-expiry-dlq' })
const expiryQueue = new sqs.Queue(stack, 'ExpiryQueue', {
  queueName: 'stockhold-hold-expiry',
  visibilityTimeout: cdk.Duration.seconds(30),
  deadLetterQueue: { queue: expiryDlq, maxReceiveCount: 5 },
})
const expire = fn('Expire', 'expire.handler')
expire.addEventSource(new SqsEventSource(expiryQueue, { batchSize: 10, reportBatchItemFailures: true }))
expiryQueue.grantSendMessages(expire) // long holds are re-delayed in 15-minute hops

const sweeper = fn('Sweeper', 'sweeper.handler')
new events.Rule(stack, 'SweepEveryMinute', {
  schedule: events.Schedule.rate(cdk.Duration.minutes(1)),
  targets: [new targets.LambdaFunction(sweeper)],
})
// @learn:end cdk-expiry

// @learn:cdk-payments
const paymentsDlq = new sqs.Queue(stack, 'PaymentsDlq', {
  queueName: 'stockhold-payment-events-dlq', retentionPeriod: cdk.Duration.days(14),
})
const paymentsQueue = new sqs.Queue(stack, 'PaymentsQueue', {
  queueName: 'stockhold-payment-events',
  visibilityTimeout: cdk.Duration.seconds(5),          // a failed attempt is retried 5 s later
  deadLetterQueue: { queue: paymentsDlq, maxReceiveCount: 3 },
})
// Function timeout <= queue visibility timeout, or a slow attempt would be redelivered while still running.
const pay = fn('Payments', 'payments.handler', {}, 5)
pay.addEventSource(new SqsEventSource(paymentsQueue, { batchSize: 10, reportBatchItemFailures: true }))
// @learn:end cdk-payments

// @learn:cdk-eventlog
const events_ = new s3.Bucket(stack, 'Events', {
  bucketName: 'stockhold-events', removalPolicy: cdk.RemovalPolicy.DESTROY,
})
const eventlog = fn('Eventlog', 'eventlog.handler', { STOCKHOLD_EVENTS_BUCKET: events_.bucketName })
events_.grantWrite(eventlog)
eventlog.addEventSource(new DynamoEventSource(table, {
  startingPosition: lambda.StartingPosition.TRIM_HORIZON, batchSize: 100, retryAttempts: 5,
  maxBatchingWindow: cdk.Duration.seconds(1),
}))
// @learn:end cdk-eventlog

new cdk.CfnOutput(stack, 'TableName', { value: table.tableName })
new cdk.CfnOutput(stack, 'ExpiryQueueUrl', { value: expiryQueue.queueUrl })

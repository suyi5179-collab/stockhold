// The whole stack as CDK. `npm run deploy:local` deploys it to LocalStack through cdklocal;
// CI runs `cdk synth` so a template that cannot synthesise fails the build.
import * as cdk from 'aws-cdk-lib'
import * as ddb from 'aws-cdk-lib/aws-dynamodb'

const app = new cdk.App()
const stack = new cdk.Stack(app, 'Stockhold', { env: { account: '000000000000', region: 'us-east-1' } })

// @learn:cdk-table
const table = new ddb.Table(stack, 'Table', {
  tableName: 'stockhold',
  partitionKey: { name: 'PK', type: ddb.AttributeType.STRING },
  sortKey: { name: 'SK', type: ddb.AttributeType.STRING },
  billingMode: ddb.BillingMode.PAY_PER_REQUEST,
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

new cdk.CfnOutput(stack, 'TableName', { value: table.tableName })

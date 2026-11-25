// Before-fix version of the table in infra/app.mjs (not used). See README.md in this folder.
// q06: the stream was added to a table that already existed in a deployed stack.
// @learn:q06-before
const table = new ddb.Table(stack, 'Table', {
  tableName: 'stockhold',
  partitionKey: { name: 'PK', type: ddb.AttributeType.STRING },
  sortKey: { name: 'SK', type: ddb.AttributeType.STRING },
  billingMode: ddb.BillingMode.PAY_PER_REQUEST,
  removalPolicy: cdk.RemovalPolicy.DESTROY,
})
// @learn:end q06-before

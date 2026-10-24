locals {
  event = var.event_hostname != ""
}

data "aws_caller_identity" "me" {}

# --- vectors: one item per article chunk, 1024-dim Cohere Embed v4, cosine ---
resource "awscc_dynamodb_table" "search" {
  table_name   = "${var.name}-vectors"
  billing_mode = "PAY_PER_REQUEST" # vector indexes require on-demand
  attribute_definitions = [
    { attribute_name = "attr", attribute_type = "S" }, # filterable inside the vector index (giveaway "filtro" round); AWS returns it first
    { attribute_name = "id", attribute_type = "S" },
  ]
  key_schema = jsonencode([{ AttributeName = "id", KeyType = "HASH" }]) # awscc takes this one as JSON text
  vector_indexes = [{
    index_name        = "vecf" # "vec" + inline filter on attr: a vector index cannot be changed in place, only replaced under a new name
    vector_attribute  = { attribute_name = "v" }
    dimensions        = 1024
    distance_function = "COSINE"
    projection        = { projection_type = "ALL" }
    search_schema     = [{ attribute_name = "attr", search_schema_element_type = "INLINE_FILTER" }]
  }]
  tags = [{ key = "project", value = var.name }]
}

# event mode: second corpus (Stack Overflow en español), same shape, toggled on the page
resource "awscc_dynamodb_table" "search_so" {
  count        = local.event ? 1 : 0
  table_name   = "${var.name}-so-vectors"
  billing_mode = "PAY_PER_REQUEST"
  attribute_definitions = [
    { attribute_name = "attr", attribute_type = "S" }, # filterable inside the vector index (giveaway "filtro" round); AWS returns it first
    { attribute_name = "id", attribute_type = "S" },
  ]
  key_schema = jsonencode([{ AttributeName = "id", KeyType = "HASH" }])
  vector_indexes = [{
    index_name        = "vecf" # "vec" + inline filter on attr: a vector index cannot be changed in place, only replaced under a new name
    vector_attribute  = { attribute_name = "v" }
    dimensions        = 1024
    distance_function = "COSINE"
    projection        = { projection_type = "ALL" }
    search_schema     = [{ attribute_name = "attr", search_schema_element_type = "INLINE_FILTER" }]
  }]
  tags = [{ key = "project", value = var.name }]
}

# event mode: doc_id -> title, the second read every store but Aurora does in the "contexto" round
resource "aws_dynamodb_table" "docs" {
  count        = local.event ? 1 : 0
  name         = "${var.name}-docs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "doc_id"
  attribute {
    name = "doc_id"
    type = "S"
  }
}

locals {
  search_arns = concat([awscc_dynamodb_table.search.arn], awscc_dynamodb_table.search_so[*].arn)
}

# --- query counter: the hard cap lives here (atomic ADD with a condition) ---
resource "aws_dynamodb_table" "counter" {
  name         = "${var.name}-counter"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
}

# --- Lambda ---
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  name               = "${var.name}-fn"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "fn" {
  statement {
    sid       = "Search"
    actions   = ["dynamodb:SearchVectors"]
    resources = concat(local.search_arns, [for a in local.search_arns : "${a}/index/*"])
  }
  statement {
    sid = "Cap"
    # counter, bet leaderboard and giveaway state (game, bets, codes, winners) all live in this small table
    actions   = ["dynamodb:UpdateItem", "dynamodb:Scan", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.counter.arn]
  }
  statement {
    sid       = "Embed"
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/${var.model_id}"]
  }
  dynamic "statement" {
    for_each = aws_dynamodb_table.docs[*].arn
    content {
      sid       = "Docs"
      actions   = ["dynamodb:BatchGetItem"]
      resources = [statement.value]
    }
  }
  dynamic "statement" {
    for_each = var.bedrock_role_arn == "" ? [] : [1]
    content {
      sid       = "EmbedCrossAccount"
      actions   = ["sts:AssumeRole"]
      resources = [var.bedrock_role_arn]
    }
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fn.arn}:*"]
  }
}

resource "aws_iam_role_policy" "fn" {
  role   = aws_iam_role.fn.id
  policy = data.aws_iam_policy_document.fn.json
}

resource "aws_cloudwatch_log_group" "fn" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 3 # no questions are logged; errors only
}

resource "aws_lambda_function" "fn" {
  function_name    = var.name
  role             = aws_iam_role.fn.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "handler.lambda_handler"
  filename         = var.lambda_zip
  source_code_hash = filebase64sha256(var.lambda_zip)
  memory_size      = local.event ? 1769 : 256 # event: 1 full vCPU, so 4 parallel searches do not queue for CPU
  timeout          = 15
  environment {
    variables = merge(var.stores_env, {
      TABLE            = awscc_dynamodb_table.search.table_name
      TABLE_SO         = local.event ? awscc_dynamodb_table.search_so[0].table_name : ""
      DOCS_TABLE       = local.event ? aws_dynamodb_table.docs[0].name : ""
      ADMIN_CODE       = var.admin_code
      COUNTER_TABLE    = aws_dynamodb_table.counter.name
      MODEL_ID         = var.model_id
      MAX_QUERIES      = tostring(var.max_queries)
      EVENT_CODE       = var.event_code
      BEDROCK_ROLE_ARN = var.bedrock_role_arn
      OPEN_AT          = var.open_at
    })
  }
  depends_on = [aws_cloudwatch_log_group.fn, aws_iam_role_policy.fn]
}

# --- optional budget alert (recommended on any personal account) ---
resource "aws_budgets_budget" "fn" {
  count        = var.budget_email == "" ? 0 : 1
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}

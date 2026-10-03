locals {
  name_prefix = "${var.project_name}-${var.environment}"
  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  dynamodb_tables = {
    approvals    = "${local.name_prefix}-approvals"
    audit_events = "${local.name_prefix}-audit-events"
    portfolios   = "${local.name_prefix}-portfolios"
    sessions     = "${local.name_prefix}-sessions"
    memory_queue = "${local.name_prefix}-memory-queue"
    preferences  = "${local.name_prefix}-preferences"
  }

  lambda_artifacts = {
    backend        = "${path.module}/${var.lambda_artifact_dir}/backend.zip"
    research_agent = "${path.module}/${var.lambda_artifact_dir}/research-agent.zip"
    sentiment_mcp  = "${path.module}/${var.lambda_artifact_dir}/sentiment-mcp.zip"
  }

  frontend_bucket_name = coalesce(var.frontend_bucket_name, "${local.name_prefix}-frontend")
}

data "aws_iam_policy_document" "backend_dynamodb" {
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Scan",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.approvals.arn,
      aws_dynamodb_table.audit_events.arn,
      aws_dynamodb_table.portfolios.arn,
      aws_dynamodb_table.sessions.arn,
      aws_dynamodb_table.memory_queue.arn,
      aws_dynamodb_table.preferences.arn,
    ]
  }
}

data "aws_iam_policy_document" "backend_bedrock" {
  statement {
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "backend_combined" {
  source_policy_documents = [
    data.aws_iam_policy_document.backend_dynamodb.json,
    data.aws_iam_policy_document.backend_bedrock.json,
  ]
}

data "aws_iam_policy_document" "agent_bedrock" {
  statement {
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = ["*"]
  }
}

resource "aws_dynamodb_table" "approvals" {
  name         = local.dynamodb_tables.approvals
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "approval_id"

  attribute {
    name = "approval_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "audit_events" {
  name         = local.dynamodb_tables.audit_events
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"

  attribute {
    name = "event_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "portfolios" {
  name         = local.dynamodb_tables.portfolios
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "account_id"

  attribute {
    name = "account_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "sessions" {
  name         = local.dynamodb_tables.sessions
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "memory_queue" {
  name         = local.dynamodb_tables.memory_queue
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "task_id"

  attribute {
    name = "task_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "preferences" {
  name         = local.dynamodb_tables.preferences
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "client_id"

  attribute {
    name = "client_id"
    type = "S"
  }

  tags = local.tags
}

resource "aws_s3_bucket" "frontend" {
  bucket = local.frontend_bucket_name
  tags   = local.tags
}

resource "aws_s3_bucket_ownership_controls" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  index_document {
    suffix = "index.html"
  }

  error_document {
    key = "index.html"
  }
}

data "aws_iam_policy_document" "frontend_public_read" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend_public_read" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_public_read.json

  depends_on = [aws_s3_bucket_public_access_block.frontend]
}

module "backend_lambda" {
  source = "../../modules/python_lambda"

  function_name   = "${local.name_prefix}-backend"
  description     = "Asset management backend API."
  artifact_path   = local.lambda_artifacts.backend
  handler         = "app.lambda_handler.handler"
  runtime         = var.lambda_runtime
  timeout_seconds = 30
  memory_mb       = 512
  policy_json     = data.aws_iam_policy_document.backend_combined.json
  attach_policy   = true
  tags            = local.tags

  environment_variables = {
    APP_NAME                              = "Asset Management API"
    ENVIRONMENT                           = var.environment
    PERSISTENCE_MODE                      = "aws"
    DYNAMODB_MODE                         = "aws"
    DYNAMODB_ENDPOINT_URL                 = ""
    APPROVALS_TABLE_NAME                  = aws_dynamodb_table.approvals.name
    AUDIT_EVENTS_TABLE_NAME               = aws_dynamodb_table.audit_events.name
    PORTFOLIOS_TABLE_NAME                 = aws_dynamodb_table.portfolios.name
    SESSIONS_TABLE_NAME                   = aws_dynamodb_table.sessions.name
    MEMORY_QUEUE_TABLE_NAME               = aws_dynamodb_table.memory_queue.name
    PREFERENCES_TABLE_NAME                = aws_dynamodb_table.preferences.name
    RESEARCH_AGENT_REMOTE_ENABLED         = "true"
    RESEARCH_AGENT_URL                    = "${module.http_api.api_endpoint}/a2a/research"
    SENTIMENT_MCP_ENABLED                 = "true"
    SENTIMENT_MCP_URL                     = "${module.http_api.api_endpoint}/mcp"
    MARKET_STREAM_MAX_EVENTS              = "20"
    SEED_DEFAULT_PORTFOLIOS               = "true"
    FEATURE_MEMORY_AGENT_LLM_ENABLED      = "true"
    FEATURE_RESEARCH_AGENT_LLM_ENABLED    = "true"
    FEATURE_SENTIMENT_AGENT_LLM_ENABLED   = "true"
    FEATURE_REBALANCING_AGENT_LLM_ENABLED = "true"
    FEATURE_RISK_AGENT_LLM_ENABLED        = "true"
    FEATURE_TRADE_PROPOSAL_AGENT_LLM_ENABLED = "true"
    FEATURE_FALLBACK_ON_LLM_FAILURE       = "true"
    API_TOKEN                             = var.api_token
  }
}

module "research_agent_lambda" {
  source = "../../modules/python_lambda"

  function_name   = "${local.name_prefix}-research-agent"
  description     = "Remote A2A research agent."
  artifact_path   = local.lambda_artifacts.research_agent
  handler         = "app.lambda_handler.handler"
  runtime         = var.lambda_runtime
  timeout_seconds = 15
  memory_mb       = 256
  policy_json     = data.aws_iam_policy_document.agent_bedrock.json
  attach_policy   = true
  tags            = local.tags

  environment_variables = {
    ENVIRONMENT                        = var.environment
    SENTIMENT_MCP_URL                  = "${module.http_api.api_endpoint}/mcp"
    FEATURE_RESEARCH_AGENT_LLM_ENABLED = "true"
  }
}

module "sentiment_mcp_lambda" {
  source = "../../modules/python_lambda"

  function_name   = "${local.name_prefix}-sentiment-mcp"
  description     = "Sentiment MCP JSON-RPC tool server."
  artifact_path   = local.lambda_artifacts.sentiment_mcp
  handler         = "app.lambda_handler.handler"
  runtime         = var.lambda_runtime
  timeout_seconds = 15
  memory_mb       = 256
  policy_json     = data.aws_iam_policy_document.agent_bedrock.json
  attach_policy   = true
  tags            = local.tags

  environment_variables = {
    ENVIRONMENT                         = var.environment
    FEATURE_SENTIMENT_AGENT_LLM_ENABLED = "true"
  }
}

module "http_api" {
  source = "../../modules/http_api"

  name               = "${local.name_prefix}-api"
  cors_allow_origins = var.cors_allow_origins
  tags               = local.tags

  routes = {
    backend_proxy = {
      route_key            = "ANY /{proxy+}"
      lambda_invoke_arn    = module.backend_lambda.invoke_arn
      lambda_function_arn  = module.backend_lambda.function_arn
      lambda_function_name = module.backend_lambda.function_name
    }
    backend_root = {
      route_key            = "ANY /"
      lambda_invoke_arn    = module.backend_lambda.invoke_arn
      lambda_function_arn  = module.backend_lambda.function_arn
      lambda_function_name = module.backend_lambda.function_name
    }
    research_agent = {
      route_key            = "ANY /a2a/research"
      lambda_invoke_arn    = module.research_agent_lambda.invoke_arn
      lambda_function_arn  = module.research_agent_lambda.function_arn
      lambda_function_name = module.research_agent_lambda.function_name
    }
    sentiment_mcp = {
      route_key            = "ANY /mcp"
      lambda_invoke_arn    = module.sentiment_mcp_lambda.invoke_arn
      lambda_function_arn  = module.sentiment_mcp_lambda.function_arn
      lambda_function_name = module.sentiment_mcp_lambda.function_name
    }
  }
}

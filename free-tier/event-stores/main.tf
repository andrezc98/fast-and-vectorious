# Applied once from the laptop with AWS_PROFILE=fv-lab (the lab account).
# The other three stores of the talk, Ley corpus only, so the event site can race all four per question.
# No VPC endpoints and no public database: Aurora sits in private subnets with no inbound rule and is reached
# only through the RDS Data API (IAM + a read-only secret); OpenSearch has a public endpoint but every request
# needs SigV4 and a data-access policy naming the role. Both scale to zero (Aurora 0 ACU after 10 min,
# OpenSearch NextGen after 10 min), so idle cost is storage only. Delete after the talk: terraform destroy.
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.68" }
    random = { source = "hashicorp/random", version = "~> 3.7" }
  }
  # Local state on purpose (git-ignored): one operator, removed after Oct 24.
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { project = "fast-and-vectorious" }
  }
}

variable "event_role_name" {
  description = "Role the event Lambda already assumes for Bedrock (free-tier/event-bedrock); it gets read-only store access too"
  type        = string
  default     = "fv-scd2026-bedrock"
}

variable "aurora_engine_version" {
  type    = string
  default = "17.11" # same as the lab (pgvector 0.8.2)
}

data "aws_caller_identity" "me" {}
data "aws_iam_session_context" "me" { arn = data.aws_caller_identity.me.arn }
data "aws_iam_role" "event" { name = var.event_role_name }
data "aws_availability_zones" "az" { state = "available" }

locals {
  name       = "fv-scd2026"
  collection = "fv-ley"
  corpora    = ["ley", "so"] # Ley Chile and Stack Overflow en español, about the same number of chunks
  indexes    = [for c in local.corpora : "${c}-cohere"]
}

# --- S3 Vectors ---
resource "aws_s3vectors_vector_bucket" "ley" {
  vector_bucket_name = "${local.name}-${data.aws_caller_identity.me.account_id}"
}

resource "aws_s3vectors_index" "ley" {
  for_each           = toset(local.indexes)
  vector_bucket_name = aws_s3vectors_vector_bucket.ley.vector_bucket_name
  index_name         = each.key
  data_type          = "float32"
  dimension          = 1024
  distance_metric    = "cosine"
}

moved {
  from = aws_s3vectors_index.ley
  to   = aws_s3vectors_index.ley["ley-cohere"]
}

# --- OpenSearch Serverless NextGen: public endpoint, IAM-only data plane ---
resource "aws_opensearchserverless_security_policy" "enc" {
  name = "${local.name}-enc"
  type = "encryption"
  policy = jsonencode({
    Rules       = [{ ResourceType = "collection", Resource = ["collection/${local.collection}"] }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "net" {
  name = "${local.name}-net"
  type = "network"
  policy = jsonencode([{
    Rules           = [{ ResourceType = "collection", Resource = ["collection/${local.collection}"] }]
    AllowFromPublic = true # reachable, not open: SigV4 + the data policy below still gate every request
  }])
}

resource "aws_opensearchserverless_access_policy" "data" {
  name = "${local.name}-data"
  type = "data"
  policy = jsonencode([
    { # the site: read only, the corpus indexes
      Rules     = [{ ResourceType = "index", Resource = [for i in local.indexes : "index/${local.collection}/${i}"], Permission = ["aoss:ReadDocument", "aoss:DescribeIndex"] }]
      Principal = [data.aws_iam_role.event.arn]
    },
    { # the operator loading from the laptop
      Rules = [
        { ResourceType = "collection", Resource = ["collection/${local.collection}"], Permission = ["aoss:*"] },
        { ResourceType = "index", Resource = ["index/${local.collection}/*"], Permission = ["aoss:*"] }
      ]
      Principal = [data.aws_iam_session_context.me.issuer_arn]
    }
  ])
}

resource "aws_opensearchserverless_collection_group" "ley" {
  name             = "${local.name}-group"
  generation       = "NEXTGEN"
  standby_replicas = "ENABLED" # required for NEXTGEN (provider check); scales to zero anyway
  capacity_limits = [{
    min_indexing_capacity_in_ocu = 0
    max_indexing_capacity_in_ocu = 2
    min_search_capacity_in_ocu   = 0
    max_search_capacity_in_ocu   = 2
  }]
}

resource "aws_opensearchserverless_collection" "ley" {
  name                  = local.collection
  type                  = "VECTORSEARCH"
  collection_group_name = aws_opensearchserverless_collection_group.ley.name
  standby_replicas      = "ENABLED"
  depends_on            = [aws_opensearchserverless_security_policy.enc, aws_opensearchserverless_security_policy.net]
}

# --- Aurora PostgreSQL Serverless v2: private subnets, no inbound rule, Data API only ---
resource "aws_vpc" "db" {
  cidr_block = "10.42.0.0/24"
  tags       = { Name = local.name }
}

resource "aws_subnet" "db" {
  count             = 2
  vpc_id            = aws_vpc.db.id
  cidr_block        = cidrsubnet(aws_vpc.db.cidr_block, 2, count.index)
  availability_zone = data.aws_availability_zones.az.names[count.index]
}

resource "aws_security_group" "db" {
  name        = "${local.name}-aurora"
  description = "No inbound rules: Aurora is reached only through the RDS Data API"
  vpc_id      = aws_vpc.db.id
}

resource "aws_db_subnet_group" "db" {
  name       = local.name
  subnet_ids = aws_subnet.db[*].id
}

resource "aws_rds_cluster" "ley" {
  cluster_identifier          = "${local.name}-aurora"
  engine                      = "aurora-postgresql"
  engine_version              = var.aurora_engine_version
  master_username             = "fvadmin"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.db.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  enable_http_endpoint        = true # RDS Data API
  skip_final_snapshot         = true
  storage_encrypted           = true
  serverlessv2_scaling_configuration {
    min_capacity             = 0
    max_capacity             = 2
    seconds_until_auto_pause = 600
  }
}

resource "aws_rds_cluster_instance" "ley" {
  identifier         = "${local.name}-aurora-1"
  cluster_identifier = aws_rds_cluster.ley.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.ley.engine
  engine_version     = aws_rds_cluster.ley.engine_version
}

# Read-only login for the site; load.py creates the Postgres role with this password.
resource "random_password" "reader" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "reader" {
  name                    = "${local.name}-aurora-reader"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "reader" {
  secret_id     = aws_secretsmanager_secret.reader.id
  secret_string = jsonencode({ username = "ley_reader", password = random_password.reader.result })
}

# --- the event role: read-only on each store ---
data "aws_iam_policy_document" "read" {
  statement {
    sid       = "S3Vectors"
    actions   = ["s3vectors:QueryVectors", "s3vectors:GetVectors"]
    resources = [for i in aws_s3vectors_index.ley : i.index_arn]
  }
  statement {
    sid       = "OpenSearch"
    actions   = ["aoss:APIAccessAll"]
    resources = [aws_opensearchserverless_collection.ley.arn]
  }
  statement {
    sid       = "AuroraDataApi"
    actions   = ["rds-data:ExecuteStatement"]
    resources = [aws_rds_cluster.ley.arn]
  }
  statement {
    sid       = "AuroraReaderSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.reader.arn]
  }
}

resource "aws_iam_role_policy" "read" {
  name   = "ley-stores-read"
  role   = data.aws_iam_role.event.name
  policy = data.aws_iam_policy_document.read.json
}

output "store_env" {
  description = "Lambda environment for the other three stores (free-tier/event.tfvars: stores_env)"
  value = {
    VECTOR_BUCKET = aws_s3vectors_vector_bucket.ley.vector_bucket_name
    AOSS_ENDPOINT = aws_opensearchserverless_collection.ley.collection_endpoint
    AURORA_ARN    = aws_rds_cluster.ley.arn
    AURORA_SECRET = aws_secretsmanager_secret.reader.arn
  }
}

output "admin_secret_arn" {
  value = aws_rds_cluster.ley.master_user_secret[0].secret_arn
}

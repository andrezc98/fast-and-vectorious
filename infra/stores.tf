data "aws_caller_identity" "me" {}

resource "aws_s3_bucket" "data" {
  bucket        = "fv-data-${data.aws_caller_identity.me.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- DynamoDB: the join table only; vector tables belong to the runner (provider issue #49350) ---
resource "aws_dynamodb_table" "docs" {
  name         = "fv-docs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "doc_id"
  attribute {
    name = "doc_id"
    type = "S"
  }
}

# --- S3 Vectors ---
resource "aws_s3vectors_vector_bucket" "lab" {
  vector_bucket_name = "fv-vectors-${data.aws_caller_identity.me.account_id}"
}

locals {
  vector_indexes = toset(concat([for c in ["so", "ley"] : [for m in var.vector_models : "${c}-${m}"]]...))
}

resource "aws_s3vectors_index" "lab" {
  for_each           = local.vector_indexes
  vector_bucket_name = aws_s3vectors_vector_bucket.lab.vector_bucket_name
  index_name         = each.key
  data_type          = "float32"
  dimension          = 1024
  distance_metric    = "cosine"
  metadata_configuration {
    non_filterable_metadata_keys = ["doc_id"]
  }
}

# --- OpenSearch Serverless NextGen (api-notes §3) ---
resource "aws_opensearchserverless_security_policy" "enc" {
  name = "fv-enc"
  type = "encryption"
  policy = jsonencode({
    Rules       = [{ ResourceType = "collection", Resource = ["collection/fv-vec"] }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "net" {
  name = "fv-net"
  type = "network"
  policy = jsonencode([{
    Rules           = [{ ResourceType = "collection", Resource = ["collection/fv-vec"] }]
    AllowFromPublic = false # only the runner's VPC endpoint reaches the data plane
    SourceVPCEs     = [aws_vpc_endpoint.interface["aoss-data"].id]
  }])
}

resource "aws_opensearchserverless_access_policy" "data" {
  name = "fv-data"
  type = "data"
  policy = jsonencode([{
    Rules = [
      { ResourceType = "collection", Resource = ["collection/fv-vec"], Permission = ["aoss:*"] },
      { ResourceType = "index", Resource = ["index/fv-vec/*"], Permission = ["aoss:*"] }
    ]
    Principal = [aws_iam_role.runner.arn]
  }])
}

resource "aws_opensearchserverless_collection_group" "lab" {
  name             = "fv-group"
  generation       = "NEXTGEN"
  standby_replicas = "ENABLED"
  # provider 6.68: list-of-object attribute, not a block (checked with `terraform providers schema`)
  capacity_limits = [{
    min_indexing_capacity_in_ocu = 0
    max_indexing_capacity_in_ocu = 4
    min_search_capacity_in_ocu   = 0
    max_search_capacity_in_ocu   = 4
  }]
}

resource "aws_opensearchserverless_collection" "lab" {
  name                  = "fv-vec"
  type                  = "VECTORSEARCH"
  collection_group_name = aws_opensearchserverless_collection_group.lab.name
  standby_replicas      = "ENABLED"
  # No vector_options: NextGen rejects any change to GPU acceleration ("GPU acceleration
  # cannot be modified for NextGen collections", CreateCollection, 2026-10-08). It stays on
  # and bills VectorIndexAccelerationOCU as its own line: report it in the cost slide.
  depends_on = [aws_opensearchserverless_security_policy.enc, aws_opensearchserverless_security_policy.net]
}

# --- Aurora PostgreSQL Serverless v2, pauses at 0 ACU ---
resource "aws_db_subnet_group" "lab" {
  name       = "fv-lab"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_rds_cluster" "lab" {
  cluster_identifier          = "fv-aurora"
  engine                      = "aurora-postgresql"
  engine_version              = var.aurora_engine_version
  master_username             = "fvadmin"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.lab.name
  vpc_security_group_ids      = [aws_security_group.aurora.id]
  skip_final_snapshot         = true
  storage_encrypted           = true
  serverlessv2_scaling_configuration {
    min_capacity             = 0
    max_capacity             = 8
    seconds_until_auto_pause = 600
  }
}

resource "aws_rds_cluster_instance" "lab" {
  identifier         = "fv-aurora-1"
  cluster_identifier = aws_rds_cluster.lab.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.lab.engine
  engine_version     = aws_rds_cluster.lab.engine_version
}

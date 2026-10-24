# Applied once, by hand, from the laptop with AWS_PROFILE=fv-lab. Creates
# what GitHub Actions needs before it can run infra/: the state bucket and the
# OIDC role. Adapted from ../redshift-redemption/infra/bootstrap/, with two
# changes: the GitHub OIDC provider is read, not created (redshift-redemption owns
# it), and IAM is granted on the two lab roles only, under a permissions boundary.
terraform {
  required_version = ">= 1.15"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
  # No backend: this root creates the bucket. Its state stays local and git-ignored.
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { project = "fast-and-vectorious" }
  }
}

variable "lab_account_id" {
  description = "12-digit POC account id. Git-ignored terraform.tfvars only; the precondition refuses any other account."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.lab_account_id))
    error_message = "lab_account_id must be 12 digits."
  }
}

variable "github_sub" {
  # Repo created after GitHub's 2026-07-15 cutoff, so the token carries the
  # immutable form repo:OWNER@OWNER-ID/REPO@REPO-ID:environment:lab
  # (https://docs.github.com/en/actions/reference/security/oidc). Set in the
  # git-ignored terraform.tfvars; no default on purpose.
  description = "OIDC sub claim allowed to assume fv-gha."
  type        = string
}

variable "alert_email" {
  description = "Budget alert recipient. Git-ignored terraform.tfvars only."
  type        = string
}

data "aws_caller_identity" "me" {}

resource "terraform_data" "account_gate" {
  input = var.lab_account_id
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.me.account_id == var.lab_account_id
      error_message = "Wrong account. Export AWS_PROFILE=fv-lab; do not widen this gate."
    }
  }
}

# --- budget: USD 80 alert, created before anything else (plan Global Constraints) ---
# Lives here, not in infra/, so it exists before the first infra apply and survives
# infra destroy. Filters on the project tag, which must be an active cost
# allocation tag (Task 0) or the budget reads USD 0.
resource "aws_budgets_budget" "lab" {
  name         = "fv-lab"
  budget_type  = "COST"
  limit_amount = "80"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$fast-and-vectorious"]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

# Fallback while the project tag is inactive: this is a linked account and only the
# payer can activate cost allocation tags (CE AccessDenied, 2026-10-08). These services
# carry ~no baseline here (Cost Explorer Sep-Oct 2026), so their total is ~the lab.
# Not covered: VPC (USD 57/month baseline, includes the endpoints), S3, Claude via Bedrock.
resource "aws_budgets_budget" "lab_services" {
  name         = "fv-lab-services"
  budget_type  = "COST"
  limit_amount = "80"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  cost_filter {
    name = "Service"
    values = ["Amazon OpenSearch Service", "Amazon SageMaker", "Amazon DynamoDB",
    "Amazon Elastic Compute Cloud - Compute", "Amazon Relational Database Service", "Amazon Bedrock"]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

# --- state bucket (versioned, SSE-S3, private, old versions expire) ---
resource "aws_s3_bucket" "tfstate" {
  depends_on    = [aws_budgets_budget.lab]
  bucket_prefix = "fv-tfstate-"
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  depends_on = [aws_s3_bucket_versioning.tfstate]
  bucket     = aws_s3_bucket.tfstate.id
  rule {
    id     = "expire-noncurrent-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# --- GitHub OIDC provider (existing) + role ---
# IAM allows one provider per URL and redshift-redemption already created it, so
# this root only reads it. Destroying this root never touches the provider.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [var.github_sub]
    }
  }
}

resource "aws_iam_role" "gha" {
  name                 = "fv-gha"
  description          = "GitHub Actions (fast-and-vectorious, environment lab) runs infra/ through OIDC"
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  max_session_duration = 3600
}

# PowerUserAccess covers DynamoDB, S3, S3 Vectors, OpenSearch Serverless, RDS,
# EC2/VPC, SageMaker and Budgets, but no IAM. infra/ creates exactly two roles
# (fv-runner, fv-sagemaker) and one instance profile (fv-runner), so IAM is
# granted on those names only. fv-gha itself is outside the grant: it can never
# edit its own policies.
resource "aws_iam_role_policy_attachment" "power" {
  role       = aws_iam_role.gha.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

# Ceiling for every role infra/ creates: no IAM except passing fv-sagemaker to
# SageMaker (the runner starts the Qwen endpoint). Even a rewritten inline policy
# on fv-runner cannot climb above PowerUser. fv-gha has no IAM write on policies,
# so it cannot edit this one.
data "aws_iam_policy_document" "boundary" {
  statement {
    sid         = "AllButIam"
    not_actions = ["iam:*", "organizations:*", "account:*"]
    resources   = ["*"]
  }
  statement {
    sid       = "PassSageMakerRole"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${var.lab_account_id}:role/fv-sagemaker"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "boundary" {
  name        = "fv-boundary"
  description = "Permissions boundary for the fast-and-vectorious lab roles"
  policy      = data.aws_iam_policy_document.boundary.json
}

locals {
  lab_roles = ["arn:aws:iam::${var.lab_account_id}:role/fv-runner", "arn:aws:iam::${var.lab_account_id}:role/fv-sagemaker"]
}

data "aws_iam_policy_document" "iam_fv" {
  # Pattern from https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html
  # (delegating with iam:PermissionsBoundary), applied to roles.
  statement {
    sid       = "WritesOnlyWithBoundary"
    actions   = ["iam:CreateRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:DetachRolePolicy"]
    resources = local.lab_roles
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.boundary.arn]
    }
  }
  statement {
    sid       = "AttachSsmOnly"
    actions   = ["iam:AttachRolePolicy"]
    resources = local.lab_roles
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.boundary.arn]
    }
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = ["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"]
    }
  }
  statement {
    sid = "ReadTagDelete"
    actions = ["iam:DeleteRole", "iam:GetRole", "iam:TagRole", "iam:UntagRole", "iam:UpdateAssumeRolePolicy",
    "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole"]
    resources = local.lab_roles
  }
  statement {
    sid = "RunnerInstanceProfile"
    actions = ["iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:GetInstanceProfile",
    "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile", "iam:TagInstanceProfile"]
    resources = ["arn:aws:iam::${var.lab_account_id}:instance-profile/fv-runner"]
  }
  # No iam:PassedToService condition: AddRoleToInstanceProfile also needs PassRole
  # (IAM API reference) and the docs do not say it carries that key. fv-runner is
  # boundary-capped, so passing it anywhere grants nothing fv-gha lacks.
  statement {
    sid       = "PassRunner"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${var.lab_account_id}:role/fv-runner"]
  }
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["rds.amazonaws.com", "observability.aoss.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "iam_fv" {
  role   = aws_iam_role.gha.id
  policy = data.aws_iam_policy_document.iam_fv.json
}

# Both go into GitHub repository variables (runbook §0). Not committed: the bucket
# name is random and the ARN carries the account id.
output "state_bucket" {
  value = aws_s3_bucket.tfstate.id
}

output "gha_role_arn" {
  value = aws_iam_role.gha.arn
}

# infra/ builds the same ARN from the account id; every aws_iam_role there sets
# permissions_boundary to it, or fv-gha's CreateRole is denied.
output "boundary_arn" {
  value = aws_iam_policy.boundary.arn
}

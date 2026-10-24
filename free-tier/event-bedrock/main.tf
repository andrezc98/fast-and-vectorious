# Applied once from the laptop with AWS_PROFILE=fv-lab (the lab account, which has Bedrock quota).
# A role the event site's Lambda (in another account, Bedrock quota 0) assumes to embed questions with
# Cohere Embed v4. Delete after the talk: terraform destroy.
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.68" }
  }
  # Local state on purpose (git-ignored): one operator, removed after Oct 24.
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { project = "fast-and-vectorious" }
  }
}

variable "event_account_id" {
  description = "Account that runs the event site (git-ignored terraform.tfvars)"
  type        = string
}

variable "event_lambda_roles" {
  description = "Lambda roles allowed to embed here (free-tier/terraform: <name>-fn): the event site and the student-path test"
  type        = list(string)
  default     = ["ley-search-fn", "ley-student-fn"]
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.event_account_id}:root"]
    }
    # Only that Lambda's role; aws:PrincipalArn lets this exist before the role does.
    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = [for r in var.event_lambda_roles : "arn:aws:iam::${var.event_account_id}:role/${r}"]
    }
  }
}

resource "aws_iam_role" "bedrock" {
  name                 = "fv-scd2026-bedrock"
  description          = "SCD Chile 2026 demo site embeds questions here (Cohere Embed v4 only)"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "embed" {
  statement {
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:us-east-1::foundation-model/cohere.embed-v4:0"]
  }
}

resource "aws_iam_role_policy" "embed" {
  role   = aws_iam_role.bedrock.id
  policy = data.aws_iam_policy_document.embed.json
}

output "role_arn" {
  value = aws_iam_role.bedrock.arn
}

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 6.68" }
    awscc      = { source = "hashicorp/awscc", version = "~> 1.105" } # DynamoDB vector index (hashicorp/aws lacks it: #49350)
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.27" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = var.name }
  }
}

provider "awscc" {
  region = var.region
}

# Only used in event mode; reads CLOUDFLARE_API_TOKEN from the environment.
provider "cloudflare" {}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  description = "Prefix for every resource"
  type        = string
  default     = "ley-search"
}

variable "model_id" {
  description = "Bedrock model for query embeddings; must match the vectors in the table"
  type        = string
  default     = "cohere.embed-v4:0"
}

variable "max_queries" {
  description = "Hard cap on questions answered, enforced before Bedrock is called"
  type        = number
  default     = 1000
}

variable "lambda_zip" {
  description = "Built by deploy.sh"
  type        = string
  default     = "../build/lambda.zip"
}

# --- event mode (the live talk) ---
variable "event_hostname" {
  description = "Empty = student mode (default execute-api URL). Set (e.g. scd2026.builtbyandres.com) = custom domain via Cloudflare"
  type        = string
  default     = ""
}

variable "event_code" {
  description = "Required ?code= on /ask in event mode"
  type        = string
  default     = ""
  sensitive   = true
}

variable "cloudflare_zone_id" {
  type    = string
  default = ""
}

variable "bedrock_role_arn" {
  description = "Event mode: role in another account to embed under (this account's Bedrock quota is 0)"
  type        = string
  default     = ""
}

variable "budget_email" {
  description = "Optional: email for a monthly budget alert (USD budget_usd, alerts at 50% actual and 100% forecast)"
  type        = string
  default     = ""
}

variable "budget_usd" {
  type    = number
  default = 5
}

variable "stores_env" {
  description = "Event mode: the other three stores (terraform output store_env in free-tier/event-stores). Empty = DynamoDB only"
  type        = map(string)
  default     = {}
}

variable "open_at" {
  description = "Event mode: epoch seconds before which /ask and /top refuse every call (empty = always open)"
  type        = string
  default     = ""
}

variable "admin_code" {
  description = "Event mode: secret that unlocks /admin (giveaway rounds). Empty = no admin"
  type        = string
  default     = ""
  sensitive   = true
}

variable "aurora_engine_version" {
  type    = string
  default = "17.11" # confirm with describe-db-engine-versions (Step 1); must ship pgvector 0.8.2
}

variable "vector_models" {
  type        = list(string)
  default     = ["cohere"] # set to the bake-off winner before apply; one S3 Vectors index per corpus and model
  description = "Embedding models whose S3 Vectors indexes exist"
}

variable "runner_enabled" {
  type    = bool
  default = true
}

variable "tei_image" {
  type        = string
  default     = ""
  description = "TEI DLC image URI for us-east-1 (api-notes §5); passed through the workflow, never committed"
}

variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS Region to deploy resources into"
}

variable "project_prefix" {
  type        = string
  default     = "concierge-conectaltel"
  description = "Prefix used for naming AWS resources"
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Deployment environment (dev, staging, prod)"
}

variable "raw_bucket_name" {
  type        = string
  default     = ""
  description = "Custom name for raw S3 bucket. If empty, a unique name will be auto-generated."
}

variable "processed_bucket_name" {
  type        = string
  default     = ""
  description = "Custom name for processed JSONL S3 bucket. If empty, a unique name will be auto-generated."
}

variable "embeddings_bucket_name" {
  type        = string
  default     = ""
  description = "Custom name for the embeddings S3 bucket (*_embedded.jsonl artifacts). If empty, a unique name will be auto-generated."
}

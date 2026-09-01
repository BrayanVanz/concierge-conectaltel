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

variable "enable_opensearch_vector_search" {
  type        = bool
  default     = true
  description = "Whether to create an AWS OpenSearch Serverless Vector Search collection for the embedding index."
}

variable "opensearch_collection_name" {
  type        = string
  default     = ""
  description = "Custom name for the Amazon OpenSearch Serverless vector collection. If empty, a name based on the project prefix will be used."
}

variable "opensearch_index_name" {
  type        = string
  default     = "concierge-vectors"
  description = "Name of the default vector index used by the project when indexing generated embeddings."
}

variable "opensearch_extra_principals" {
  type        = list(string)
  default     = []
  description = "Extra IAM principal ARNs (users/roles) granted read/write access to the OpenSearch Serverless collection and index, in addition to the Lambda execution role. Useful for developers running the indexing script manually from their own AWS session."
}

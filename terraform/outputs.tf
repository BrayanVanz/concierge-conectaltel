output "raw_bucket_name" {
  value       = aws_s3_bucket.raw.id
  description = "Name of the raw corpus S3 bucket"
}

output "raw_bucket_arn" {
  value       = aws_s3_bucket.raw.arn
  description = "ARN of the raw corpus S3 bucket"
}

output "processed_bucket_name" {
  value       = aws_s3_bucket.processed.id
  description = "Name of the processed JSONL S3 bucket"
}

output "processed_bucket_arn" {
  value       = aws_s3_bucket.processed.arn
  description = "ARN of the processed JSONL S3 bucket"
}

output "embeddings_bucket_name" {
  value       = aws_s3_bucket.embeddings.id
  description = "Name of the embeddings S3 bucket (*_embedded.jsonl artifacts)"
}

output "embeddings_bucket_arn" {
  value       = aws_s3_bucket.embeddings.arn
  description = "ARN of the embeddings S3 bucket"
}

output "chunking_lambda_name" {
  value       = aws_lambda_function.chunking.function_name
  description = "Name of the chunking Lambda function"
}

output "chunking_lambda_arn" {
  value       = aws_lambda_function.chunking.arn
  description = "ARN of the chunking Lambda function"
}

output "embeddings_lambda_name" {
  value       = aws_lambda_function.embeddings.function_name
  description = "Name of the embeddings Lambda function"
}

output "embeddings_lambda_arn" {
  value       = aws_lambda_function.embeddings.arn
  description = "ARN of the embeddings Lambda function"
}
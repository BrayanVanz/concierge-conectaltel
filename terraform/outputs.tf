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

output "lambda_function_name" {
  value       = aws_lambda_function.ingestion.function_name
  description = "Name of the ingestion Lambda function"
}

output "lambda_function_arn" {
  value       = aws_lambda_function.ingestion.arn
  description = "ARN of the ingestion Lambda function"
}

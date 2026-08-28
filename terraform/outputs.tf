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

output "chunking_lambda_name" {
  value       = aws_lambda_function.chunking.function_name
  description = "Name of the chunking Lambda function"
}

output "chunking_lambda_arn" {
  value       = aws_lambda_function.chunking.arn
  description = "ARN of the chunking Lambda function"
}
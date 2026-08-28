# Package source code into zip archive for Lambda deployment
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../src/ingestion.py"
  output_path = "${path.module}/lambda_function.zip"
}

# CloudWatch Log Group for Lambda
resource "aws_cloudwatch_log_group" "lambda_log_group" {
  name              = "/aws/lambda/${var.project_prefix}-ingestion"
  retention_in_days = 14
}

# AWS Lambda Function
resource "aws_lambda_function" "ingestion" {
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  function_name    = "${var.project_prefix}-ingestion"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "ingestion.lambda_handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 256

  environment {
    variables = {
      RAW_BUCKET_NAME       = aws_s3_bucket.raw.id
      PROCESSED_BUCKET_NAME = aws_s3_bucket.processed.id
      OUTPUT_KEY            = "corpus.jsonl"
      INPUT_PREFIX          = "corpus/"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda_log_group,
    aws_iam_role_policy_attachment.lambda_attach
  ]
}

# Permission allowing Raw S3 Bucket to trigger Lambda
resource "aws_lambda_permission" "allow_s3_raw" {
  statement_id  = "AllowExecutionFromS3Raw"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestion.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw.arn
}

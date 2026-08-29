# Package source code into zip archive for Lambda deployment
data "archive_file" "lambda_zip" {
  type        = "zip"
  source {
    content  = file("${path.module}/../src/chunking/lambda_function.py")
    filename = "lambda_function.py"
  }
  source {
    content  = file("${path.module}/../src/chunking/__init__.py")
    filename = "__init__.py"
  }
  source {
    content  = file("${path.module}/../src/chunking/chunk_strategies.py")
    filename = "chunk_strategies.py"
  }
  source {
    content  = file("${path.module}/../src/chunking/payload_formatter.py")
    filename = "payload_formatter.py"
  }
  source {
    content  = file("${path.module}/../src/chunking/reader.py")
    filename = "reader.py"
  }
  source {
    content  = file("${path.module}/../src/chunking/s3_writer.py")
    filename = "s3_writer.py"
  }
  output_path = "${path.module}/lambda_function.zip"
}

resource "aws_cloudwatch_log_group" "lambda_log_group" {
  name              = "/aws/lambda/${var.project_prefix}-chunking" 
  retention_in_days = 14
  
  lifecycle {
    ignore_changes = [name]
  }
}

# AWS Lambda Function
resource "aws_lambda_function" "chunking" {
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  function_name    = "${var.project_prefix}-chunking" 
  role             = aws_iam_role.lambda_exec.arn
  handler          = "lambda_function.lambda_handler" 
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 256

  environment {
    variables = {
      INPUT_BUCKET_NAME  = var.processed_bucket_name != "" ? var.processed_bucket_name : aws_s3_bucket.processed.id
      OUTPUT_BUCKET_NAME = var.processed_bucket_name != "" ? var.processed_bucket_name : aws_s3_bucket.processed.id
      INPUT_PREFIX       = "cleaned/"
      OUTPUT_PREFIX      = "chunks/"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda_log_group,
    aws_iam_role_policy_attachment.lambda_attach
  ]
}

# Permission allowing Processed S3 Bucket to trigger Lambda
resource "aws_lambda_permission" "allow_s3_processed" {
  statement_id  = "AllowExecutionFromS3Processed"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.chunking.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.processed.arn
}
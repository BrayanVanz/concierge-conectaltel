# ---------------------------------------------------------------------------
# Módulo de ingestão (src/ingestion.py) — lê os .md de raw/corpus/, monta os
# registros JSONL com metadados (incluindo "source") e grava em
# processed/cleaned/corpus.jsonl. Estágio que faltava no Terraform: sem ele
# o pipeline nunca saía do bucket raw.
# ---------------------------------------------------------------------------

data "archive_file" "ingestion_zip" {
  type = "zip"
  source {
    content  = file("${path.module}/../src/ingestion.py")
    filename = "ingestion.py"
  }
  output_path = "${path.module}/ingestion_function.zip"
}

resource "aws_cloudwatch_log_group" "ingestion_log_group" {
  name              = "/aws/lambda/${var.project_prefix}-ingestion"
  retention_in_days = 14

  lifecycle {
    ignore_changes = [name]
  }
}

resource "aws_lambda_function" "ingestion" {
  filename         = data.archive_file.ingestion_zip.output_path
  source_code_hash = data.archive_file.ingestion_zip.output_base64sha256
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
      INPUT_PREFIX          = "corpus/"
      OUTPUT_KEY             = "cleaned/corpus.jsonl"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.ingestion_log_group,
    aws_iam_role_policy_attachment.lambda_attach
  ]
}

# Permite que o bucket `raw` dispare a Lambda de ingestão.
resource "aws_lambda_permission" "allow_s3_ingestion" {
  statement_id  = "AllowExecutionFromS3Raw"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestion.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw.arn
}

# ---------------------------------------------------------------------------
# Módulo de chunking (src/chunking) — lê processed/cleaned/corpus.jsonl,
# aplica as 3 estratégias e grava em processed/chunks/.
# ---------------------------------------------------------------------------

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
      # Antes usava var.processed_bucket_name (sem sufixo), que não batia
      # com o nome real do bucket criado em s3.tf. Agora referencia o
      # recurso diretamente, então nunca dessincroniza.
      INPUT_BUCKET_NAME  = aws_s3_bucket.processed.id
      OUTPUT_BUCKET_NAME = aws_s3_bucket.processed.id
      INPUT_PREFIX       = "cleaned/"
      OUTPUT_PREFIX      = "chunks/"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda_log_group,
    aws_iam_role_policy_attachment.lambda_attach
  ]
}

# Permissão: bucket `processed` (prefixo cleaned/) dispara a Lambda de chunking.
resource "aws_lambda_permission" "allow_s3_processed_chunking" {
  statement_id  = "AllowExecutionFromS3ProcessedChunking"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.chunking.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.processed.arn
}

# ---------------------------------------------------------------------------
# Módulo de embeddings (src/embeddings) — mesmo padrão da Lambda de chunking.
# Lê os chunks_*.jsonl do bucket `processed` (prefixo chunks/), gera os
# vetores via Bedrock (Cohere Embed v4) e grava *_embedded.jsonl no bucket
# de embeddings. O runner local equivalente é src/embeddings/main.py.
# ---------------------------------------------------------------------------

data "archive_file" "embeddings_zip" {
  type = "zip"
  source {
    content  = file("${path.module}/../src/embeddings/lambda_function.py")
    filename = "lambda_function.py"
  }
  source {
    content  = file("${path.module}/../src/embeddings/embedder.py")
    filename = "embedder.py"
  }
  output_path = "${path.module}/embeddings_function.zip"
}

resource "aws_cloudwatch_log_group" "embeddings_log_group" {
  name              = "/aws/lambda/${var.project_prefix}-embeddings"
  retention_in_days = 14

  lifecycle {
    ignore_changes = [name]
  }
}

resource "aws_lambda_function" "embeddings" {
  filename         = data.archive_file.embeddings_zip.output_path
  source_code_hash = data.archive_file.embeddings_zip.output_base64sha256
  function_name    = "${var.project_prefix}-embeddings"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 512

  environment {
    variables = {
      # Idem: referenciando os buckets reais em vez de var.*_bucket_name.
      INPUT_BUCKET_NAME  = aws_s3_bucket.processed.id
      OUTPUT_BUCKET_NAME = aws_s3_bucket.embeddings.id
      INPUT_PREFIX       = "chunks/"
      OUTPUT_PREFIX      = ""
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.embeddings_log_group,
    aws_iam_role_policy_attachment.lambda_attach
  ]
}

# Permite que o bucket `processed` (prefixo chunks/) dispare a Lambda de embeddings.
resource "aws_lambda_permission" "allow_s3_embeddings" {
  statement_id  = "AllowExecutionFromS3Chunks"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.embeddings.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.processed.arn
}
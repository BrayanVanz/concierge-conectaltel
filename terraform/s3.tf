# Declaração do sufixo aleatório, usado apenas quando nenhum nome customizado é informado
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# 1. Bucket de Dados Brutos (Raw)
resource "aws_s3_bucket" "raw" {
  bucket        = var.raw_bucket_name != "" ? var.raw_bucket_name : "${var.project_prefix}-raw-${random_id.bucket_suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "raw_versioning" {
  bucket = aws_s3_bucket.raw.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw_crypto" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "raw_pab" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 2. Bucket de Dados Processados
resource "aws_s3_bucket" "processed" {
  bucket        = var.processed_bucket_name != "" ? var.processed_bucket_name : "${var.project_prefix}-processed-${random_id.bucket_suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "processed_versioning" {
  bucket = aws_s3_bucket.processed.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "processed_crypto" {
  bucket = aws_s3_bucket.processed.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "processed_pab" {
  bucket                  = aws_s3_bucket.processed.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 3. Bucket de Embeddings
resource "aws_s3_bucket" "embeddings" {
  bucket        = var.embeddings_bucket_name != "" ? var.embeddings_bucket_name : "${var.project_prefix}-embeddings-${random_id.bucket_suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "embeddings_versioning" {
  bucket = aws_s3_bucket.embeddings.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "embeddings_crypto" {
  bucket = aws_s3_bucket.embeddings.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "embeddings_pab" {
  bucket                  = aws_s3_bucket.embeddings.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 4. Notificação: bucket RAW -> Lambda de ingestão
#    Dispara quando um .md é enviado em raw/corpus/, gerando
#    processed/cleaned/corpus.jsonl (consumido pela Lambda de chunking).
resource "aws_s3_bucket_notification" "raw_bucket_notification" {
  bucket = aws_s3_bucket.raw.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingestion.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "corpus/"
    filter_suffix       = ".md"
  }

  depends_on = [aws_lambda_permission.allow_s3_ingestion]
}

# 5. Notificação: bucket PROCESSED -> Lambda de chunking (prefixo cleaned/)
#    e Lambda de embeddings (prefixo chunks/). Um único recurso de
#    notificação por bucket, com filtros de prefixo para rotear cada
#    Lambda para o estágio correto do pipeline.
resource "aws_s3_bucket_notification" "processed_bucket_notification" {
  bucket = aws_s3_bucket.processed.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.chunking.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "cleaned/"
    filter_suffix       = ".jsonl"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.embeddings.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "chunks/"
    filter_suffix       = ".jsonl"
  }

  depends_on = [
    aws_lambda_permission.allow_s3_processed_chunking,
    aws_lambda_permission.allow_s3_embeddings
  ]
}
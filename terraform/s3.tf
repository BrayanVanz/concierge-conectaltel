resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# Raw Data S3 Bucket (for raw markdown files)
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

# Processed Data S3 Bucket (for jsonl output)
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

# Embeddings S3 Bucket (arquivos *_embedded.jsonl, saída do módulo de embeddings)
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

# S3 Event Notifications no bucket `processed`.
# Um bucket só pode ter UM recurso aws_s3_bucket_notification, então as duas
# Lambdas ficam neste mesmo bloco, cada uma com seu filtro de prefixo.
resource "aws_s3_bucket_notification" "processed_bucket_notification" {
  bucket = aws_s3_bucket.processed.id

  # chunking: dispara quando um corpus.jsonl aparece em cleaned/
  lambda_function {
    lambda_function_arn = aws_lambda_function.chunking.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "cleaned/"
    filter_suffix       = ".jsonl"
  }

  # embeddings: dispara quando um chunks_*.jsonl aparece em chunks/
  # (a saída vai para o bucket `embeddings`, então não há loop)
  lambda_function {
    lambda_function_arn = aws_lambda_function.embeddings.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "chunks/"
    filter_suffix       = ".jsonl"
  }

  depends_on = [
    aws_lambda_permission.allow_s3_processed,
    aws_lambda_permission.allow_s3_embeddings,
  ]
}
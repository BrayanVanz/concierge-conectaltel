# Bucket dedicado ao backup da trilha de auditoria (Parte 5)
#
# A trilha de auditoria em si é local (data/audit/audit_log.jsonl) e não
# depende deste bucket para funcionar — a consulta por trace_id (requisito
# de reconstrução em até 60s) continua lendo direto do disco, sem depender
# de nenhum serviço externo. Este bucket serve só como cópia de backup/
# consolidação entre máquinas da squad, via
# src/audit/upload_audit_log_to_s3.py.
#
# Fica separado dos buckets do pipeline de dados (raw/processed/embeddings
# em s3.tf) porque tem um propósito e um ciclo de vida diferentes: log de
# auditoria cresce ao longo do tempo e não é reprocessado como os outros.
resource "aws_s3_bucket" "audit_logs" {
  bucket        = var.audit_bucket_name != "" ? var.audit_bucket_name : "${var.project_prefix}-audit-logs-${random_id.bucket_suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "audit_logs_versioning" {
  bucket = aws_s3_bucket.audit_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit_logs_crypto" {
  bucket = aws_s3_bucket.audit_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "audit_logs_pab" {
  bucket                  = aws_s3_bucket.audit_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

resource "aws_s3_bucket" "loan_docs" {
  bucket = "${var.project}-${var.environment}-loan-docs"

  tags = merge(var.tags, {
    Name        = "${var.project}-${var.environment}-loan-docs"
    DataClass   = "confidential"
    ContainsPII = "true"
  })
}

resource "aws_s3_bucket_versioning" "loan_docs" {
  bucket = aws_s3_bucket.loan_docs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "loan_docs" {
  bucket = aws_s3_bucket.loan_docs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "loan_docs" {
  bucket = aws_s3_bucket.loan_docs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "loan_docs" {
  bucket = aws_s3_bucket.loan_docs.id

  rule {
    id     = "transition-to-ia"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 365
      storage_class = "GLACIER"
    }
  }
}

# enforce TLS only
resource "aws_s3_bucket_policy" "loan_docs_tls" {
  bucket = aws_s3_bucket.loan_docs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EnforceTLS"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.loan_docs.arn,
        "${aws_s3_bucket.loan_docs.arn}/*"
      ]
      Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
    }]
  })
}

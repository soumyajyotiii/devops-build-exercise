output "bucket_name" {
  value = aws_s3_bucket.loan_docs.id
}

output "bucket_arn" {
  value = aws_s3_bucket.loan_docs.arn
}

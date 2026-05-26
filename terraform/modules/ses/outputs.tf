output "domain_identity_arn" {
  value = aws_ses_domain_identity.main.arn
}

output "dkim_tokens" {
  value = aws_ses_domain_dkim.main.dkim_tokens
}

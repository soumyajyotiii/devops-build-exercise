output "kms_key_arn" {
  value = aws_kms_key.main.arn
}

output "kms_key_id" {
  value = aws_kms_key.main.key_id
}

output "pod_role_arn" {
  value = aws_iam_role.pod_role.arn
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy_role.arn
}

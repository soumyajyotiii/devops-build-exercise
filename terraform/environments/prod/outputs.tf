output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "ecr_repository_url" {
  value = aws_ecr_repository.main.repository_url
}

output "rds_endpoint" {
  value = module.rds.endpoint
}

output "s3_bucket" {
  value = module.s3.bucket_name
}

output "deploy_role_arn" {
  value = module.iam.deploy_role_arn
}

output "pod_role_arn" {
  value = module.iam.pod_role_arn
}

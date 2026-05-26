output "endpoint" {
  value = aws_db_instance.main.endpoint
}

output "database_url" {
  value     = "postgresql+psycopg://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}/${var.db_name}"
  sensitive = true
}

output "security_group_id" {
  value = aws_security_group.rds.id
}

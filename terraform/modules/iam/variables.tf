variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "oidc_provider_arn" {
  type = string
}

variable "oidc_provider_url" {
  type = string
}

variable "s3_bucket_arn" {
  type = string
}

variable "ses_from_address" {
  type    = string
  default = "loans@saaffinance.com"
}

variable "github_repo" {
  type    = string
  default = "soumyajyotiii/devops-build-exercise"
}

variable "tags" {
  type    = map(string)
  default = {}
}

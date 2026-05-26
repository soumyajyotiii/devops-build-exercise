terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "saaf-terraform-state"
    key            = "underwriting-agent/staging/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "underwriting-agent"
      Environment = "staging"
      ManagedBy   = "terraform"
    }
  }
}

locals {
  project      = "underwriting-agent"
  environment  = "staging"
  cluster_name = "${local.project}-${local.environment}"
}

module "iam" {
  source = "../../modules/iam"

  project           = local.project
  environment       = local.environment
  aws_region        = var.aws_region
  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url
  s3_bucket_arn     = module.s3.bucket_arn

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

module "vpc" {
  source = "../../modules/vpc"

  project            = local.project
  environment        = local.environment
  vpc_cidr           = "10.2.0.0/16"
  cluster_name       = local.cluster_name
  single_nat_gateway = true

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

module "eks" {
  source = "../../modules/eks"

  cluster_name           = local.cluster_name
  kubernetes_version     = "1.29"
  vpc_id                 = module.vpc.vpc_id
  private_subnet_ids     = module.vpc.private_subnet_ids
  kms_key_arn            = module.iam.kms_key_arn
  endpoint_public_access = false

  node_instance_types = ["t3.large"]
  node_desired_size   = 2
  node_min_size       = 2
  node_max_size       = 6

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

module "rds" {
  source = "../../modules/rds"

  project             = local.project
  environment         = local.environment
  vpc_id              = module.vpc.vpc_id
  private_subnet_ids  = module.vpc.private_subnet_ids
  allowed_security_groups = [module.eks.cluster_security_group_id]
  kms_key_arn         = module.iam.kms_key_arn

  instance_class        = "db.t3.large"
  allocated_storage     = 30
  max_allocated_storage = 100
  multi_az              = true  # staging mirrors prod topology
  backup_retention_days = 7
  db_password           = var.db_password

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

module "s3" {
  source = "../../modules/s3"

  project     = local.project
  environment = local.environment
  kms_key_arn = module.iam.kms_key_arn

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

module "ses" {
  source = "../../modules/ses"

  project     = local.project
  environment = local.environment

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

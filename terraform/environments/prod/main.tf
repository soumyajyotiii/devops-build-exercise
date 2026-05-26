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
    key            = "underwriting-agent/prod/terraform.tfstate"
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
      Environment = "prod"
      ManagedBy   = "terraform"
    }
  }
}

locals {
  project      = "underwriting-agent"
  environment  = "prod"
  cluster_name = "${local.project}-${local.environment}"
}

# --- IAM + KMS (has to come first since other modules need the KMS key) ---
# note: we create a minimal KMS key first, then pass it around

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

# --- VPC ---

module "vpc" {
  source = "../../modules/vpc"

  project            = local.project
  environment        = local.environment
  vpc_cidr           = "10.0.0.0/16"
  cluster_name       = local.cluster_name
  single_nat_gateway = false  # one NAT per AZ for HA in prod

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

# --- EKS ---

module "eks" {
  source = "../../modules/eks"

  cluster_name           = local.cluster_name
  kubernetes_version     = "1.29"
  vpc_id                 = module.vpc.vpc_id
  private_subnet_ids     = module.vpc.private_subnet_ids
  kms_key_arn            = module.iam.kms_key_arn
  endpoint_public_access = false

  node_instance_types = ["t3.large"]
  node_desired_size   = 3
  node_min_size       = 2
  node_max_size       = 8

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

# --- RDS ---

module "rds" {
  source = "../../modules/rds"

  project             = local.project
  environment         = local.environment
  vpc_id              = module.vpc.vpc_id
  private_subnet_ids  = module.vpc.private_subnet_ids
  allowed_security_groups = [module.eks.cluster_security_group_id]
  kms_key_arn         = module.iam.kms_key_arn

  instance_class        = "db.r6g.large"
  allocated_storage     = 50
  max_allocated_storage = 500
  multi_az              = true
  backup_retention_days = 30   # RPO is 1 hour but we keep 30 days of snapshots
  db_password           = var.db_password

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

# --- S3 ---

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

# --- SES ---

module "ses" {
  source = "../../modules/ses"

  project     = local.project
  environment = local.environment

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

# --- ECR repository ---

resource "aws_ecr_repository" "main" {
  name                 = local.project
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = module.iam.kms_key_arn
  }

  tags = {
    Project     = local.project
    Environment = local.environment
  }
}

resource "aws_ecr_lifecycle_policy" "main" {
  repository = aws_ecr_repository.main.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = {
        type = "expire"
      }
    }]
  })
}

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

resource "aws_ses_domain_identity" "main" {
  domain = var.domain
}

resource "aws_ses_domain_dkim" "main" {
  domain = aws_ses_domain_identity.main.domain
}

resource "aws_ses_email_identity" "from" {
  email = var.from_address
}

# configuration set for tracking + bounce handling
resource "aws_ses_configuration_set" "main" {
  name = "${var.project}-${var.environment}"

  delivery_options {
    tls_policy = "Require"
  }
}

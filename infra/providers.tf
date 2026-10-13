provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "qmc"
      Env       = var.env
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

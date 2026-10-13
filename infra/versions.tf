terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Path supplied by the Makefile: -backend-config="path=$HOME/.qmc/state/$(ENV).tfstate" (D20, D33).
  backend "local" {}
}

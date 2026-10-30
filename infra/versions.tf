# Works unchanged with OpenTofu (`tofu`) or Terraform (`terraform`); both
# consume the same provider registry.
terraform {
  required_version = ">= 1.8"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.11"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

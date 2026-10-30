terraform {
  required_version = ">= 1.8"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 5.11" }
    azuread = { source = "hashicorp/azuread", version = "~> 3.10" }
    azapi   = { source = "Azure/azapi", version = "~> 2.13" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

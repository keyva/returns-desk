variable "subscription_id" { type = string }

variable "location" {
  description = "Region for the hosting resources. Container Apps need not be in the Foundry account's region."
  type        = string
  default     = "eastus"
}

variable "resource_group_name" {
  description = "Resource group that holds the Foundry account (from the root layer). Hosting resources go here too."
  type        = string
}

variable "foundry_account_name" {
  description = "Foundry account name (from the root layer)."
  type        = string
}

variable "deployment_name" {
  description = "Model deployment name the app calls."
  type        = string
  default     = "gpt-6-luna"
}

variable "model_name" {
  description = "Model name, used for the app's price table."
  type        = string
  default     = "gpt-6-luna"
}

variable "name_prefix" {
  type    = string
  default = "returnsdesk"
}

variable "image_tag" {
  description = "Tag of the returns-desk image in the registry, such as v1. Build it with `az acr build` before the full apply. No default: a fixed tag means a new image only goes live through an apply."
  type        = string
}

variable "require_signin" {
  description = "Put Entra ID sign-in in front of the board. The app has no auth of its own: false lets anyone on the internet pull requests, resolve them, reset the demo and spend model tokens."
  type        = bool
  default     = true
}

variable "signin_principal_ids" {
  description = "Object ids of the users (or, with Entra ID P1, groups) allowed to sign in. Empty means only the identity running the apply."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = { project = "returns-desk", purpose = "foundry-iac-demo", layer = "hosting" }
}

variable "subscription_id" {
  description = "Azure subscription to deploy into."
  type        = string
}

variable "location" {
  description = "Azure region. East US: the Decisions API the probabilistic engine calls is not rolled out in East US 2."
  type        = string
  default     = "eastus"
}

variable "name_prefix" {
  description = "Lowercase prefix for resource names. A random suffix is appended to keep the Foundry subdomain globally unique."
  type        = string
  default     = "returnsdesk"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,14}$", var.name_prefix))
    error_message = "name_prefix must be 3-15 lowercase letters/digits and start with a letter."
  }
}

variable "existing_resource_group_name" {
  description = "Adopt mode: name of an existing resource group to import instead of creating one. Leave empty to create fresh resources."
  type        = string
  default     = ""
}

variable "existing_account_name" {
  description = "Adopt mode: name of an existing Foundry account (kind AIServices) in that resource group to import. Its custom subdomain must equal its name. Leave empty to create fresh resources."
  type        = string
  default     = ""

  validation {
    condition     = (var.existing_account_name == "") == (var.existing_resource_group_name == "")
    error_message = "Set both existing_resource_group_name and existing_account_name, or neither."
  }
}

variable "model_name" {
  description = "OpenAI model to deploy on the Foundry resource."
  type        = string
  default     = "gpt-6-luna"
}

variable "model_version" {
  description = "Model version string as listed in the Foundry model catalog."
  type        = string
  default     = "2026-09-22"
}

variable "model_capacity" {
  description = "Deployment quota in thousands of tokens per minute. 10 = 10K TPM, plenty for a demo. Quota is a rate limit, not a charge."
  type        = number
  default     = 10
}

variable "local_auth_enabled" {
  description = "Allow API-key auth on the Foundry resource. Off by default so the demo runs Entra-only; turn on to show the key path side by side."
  type        = bool
  default     = false
}

variable "assign_caller_role" {
  description = "Grant the identity running the apply a data-plane role on the account so the app can call the model with an Entra token. Needs Owner or User Access Administrator on the resource group."
  type        = bool
  default     = true
}

variable "caller_role" {
  description = "Data-plane role for the caller. 'Foundry User' covers every Foundry data operation including the Decisions API; 'Cognitive Services OpenAI User' lists specific operations and does not include decisions."
  type        = string
  default     = "Foundry User"
}

variable "budget_amount_usd" {
  description = "Monthly cost budget for the resource group. Alerts only; Azure does not stop spend when a budget is hit."
  type        = number
  default     = 5
}

variable "budget_contact_email" {
  description = "Email that receives budget alerts. Leave empty to skip creating the budget."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default = {
    project = "returns-desk"
    purpose = "foundry-iac-demo"
  }
}

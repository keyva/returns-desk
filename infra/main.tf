# ---------------------------------------------------------------------------
# Returns Desk: a minimal Microsoft Foundry footprint.
#
# Four things get created, in dependency order:
#   1. a resource group
#   2. the Foundry resource (ARM type Microsoft.CognitiveServices/accounts,
#      kind AIServices, project management enabled)
#   3. a model deployment on that resource (pay-per-token, GlobalStandard)
#   4. a Foundry project under the resource
#
# Plus two cost/auth niceties: a role assignment so the deployer can call the
# model with an Entra token, and an optional resource-group budget alert.
# ---------------------------------------------------------------------------

resource "random_string" "suffix" {
  length  = 5
  lower   = true
  upper   = false
  numeric = true
  special = false
}

locals {
  adopt        = var.existing_account_name != ""
  base_name    = local.adopt ? var.existing_account_name : "${var.name_prefix}${random_string.suffix.result}"
  rg_name      = local.adopt ? var.existing_resource_group_name : "rg-${local.base_name}"
  account_name = local.base_name
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "rg" {
  name     = local.rg_name
  location = var.location
  tags     = var.tags

  lifecycle {
    ignore_changes = [tags]
  }
}

# The Foundry resource. The portal calls this "Microsoft Foundry"; ARM and the
# provider still call it a Cognitive Services account of kind AIServices.
resource "azurerm_cognitive_account" "foundry" {
  name                = local.account_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  kind                = "AIServices"
  sku_name            = "S0" # no fixed charge; billing is per token

  # Required for Foundry projects, Entra-token auth and the
  # https://<name>.openai.azure.com endpoint the OpenAI SDK expects.
  custom_subdomain_name      = local.account_name
  project_management_enabled = true

  # Entra ID auth by default: with local auth off the account has no API
  # keys at all, so there is nothing to leak from a laptop or a CI log.
  # var.local_auth_enabled = true turns keys on for the side-by-side demo.
  local_auth_enabled = var.local_auth_enabled

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags

  lifecycle {
    # In adopt mode the account may have tags set elsewhere; leave them alone.
    ignore_changes = [tags, network_acls] # the adopted account also carries ACLs the config never set
  }
}

# One model deployment. GlobalStandard = pay-as-you-go tokens, no hourly cost.
# The expensive SKUs are the *ProvisionedManaged ones; avoid them for a demo.
resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.model_name
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  model {
    format  = "OpenAI"
    name    = var.model_name
    version = var.model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.model_capacity
  }

  # Let Azure roll the deployment forward when a new default version ships.
  version_upgrade_option = "OnceNewDefaultVersionAvailable"
}

# A Foundry project: the unit the portal and evaluations hang off.
# The app in this repo only needs the deployment, but the project is what
# turns the account into something you can open in the Foundry portal.
resource "azurerm_cognitive_account_project" "returns_desk" {
  name                 = "returns-desk"
  cognitive_account_id = azurerm_cognitive_account.foundry.id
  location             = azurerm_resource_group.rg.location
  display_name         = "Returns Desk"
  description          = "Returns desk demo: policy rules, two engines read the statement, a clerk decides."

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

# Lets whoever ran `apply` call the model with `az login` credentials.
# Data-plane role, scoped to this one account. Foundry User (formerly Azure AI
# User) is the role Microsoft recommends; the narrower Cognitive Services
# OpenAI User role does not cover the Decisions API.
resource "azurerm_role_assignment" "caller_openai_user" {
  count = var.assign_caller_role ? 1 : 0

  scope                = azurerm_cognitive_account.foundry.id
  role_definition_name = var.caller_role
  principal_id         = data.azurerm_client_config.current.object_id
}

# Budget alert on the resource group. Purely informational, but it is the
# cheapest way to learn what the demo actually costs.
resource "azurerm_consumption_budget_resource_group" "demo" {
  count = var.budget_contact_email == "" ? 0 : 1

  name              = "budget-${local.base_name}"
  resource_group_id = azurerm_resource_group.rg.id
  amount            = var.budget_amount_usd
  time_grain        = "Monthly"

  time_period {
    # Budgets must start on the first of a month.
    start_date = formatdate("YYYY-MM-'01T00:00:00Z'", timestamp())
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = [var.budget_contact_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = [var.budget_contact_email]
  }

  lifecycle {
    # timestamp() changes every plan; don't churn the budget over it.
    ignore_changes = [time_period]
  }
}

# ---------------------------------------------------------------------------
# Hosting layer: runs the Returns Desk board on Azure Container Apps.
#
#   registry  <- image built by `az acr build`
#   identity  -> AcrPull on the registry, Foundry User on the Foundry account;
#                both engines call Foundry with this identity's Entra token, no keys
#   container app (1 replica, public HTTPS ingress)
#   Entra sign-in on the app (Container Apps built-in auth), on by default; the app has no
#   auth of its own, so turning it off opens the board to the internet
#
# The Foundry account itself is owned by the root layer and only read here.
# ---------------------------------------------------------------------------

data "azurerm_client_config" "current" {}

data "azurerm_resource_group" "rg" {
  name = var.resource_group_name
}

data "azurerm_cognitive_account" "foundry" {
  name                = var.foundry_account_name
  resource_group_name = var.resource_group_name
}

resource "random_string" "suffix" {
  length  = 5
  lower   = true
  upper   = false
  numeric = true
  special = false
}

locals {
  base             = "${var.name_prefix}${random_string.suffix.result}"
  app_name         = "ca-${var.name_prefix}"
  foundry_endpoint = "https://${var.foundry_account_name}.openai.azure.com/"
  app_fqdn         = "${local.app_name}.${azurerm_container_app_environment.env.default_domain}"
}

# ---- logs + environment ----------------------------------------------------

resource "azurerm_log_analytics_workspace" "logs" {
  name                = "log-${local.base}"
  location            = var.location
  resource_group_name = data.azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_container_app_environment" "env" {
  name                       = "cae-${local.base}"
  location                   = var.location
  resource_group_name        = data.azurerm_resource_group.rg.name
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.logs.id
  tags                       = var.tags
}

# ---- registry + identity ---------------------------------------------------

resource "azurerm_container_registry" "acr" {
  name                = "acr${local.base}" # letters and digits only, globally unique
  location            = var.location
  resource_group_name = data.azurerm_resource_group.rg.name
  sku                 = "Basic"
  admin_enabled       = false # pulls use the managed identity, not a password
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.base}"
  location            = var.location
  resource_group_name = data.azurerm_resource_group.rg.name
  tags                = var.tags
}

resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# The app's own identity gets the data-plane role on the Foundry account.
# This is the role assignment that replaces `az login` on a laptop.
resource "azurerm_role_assignment" "app_foundry_user" {
  scope                = data.azurerm_cognitive_account.foundry.id
  role_definition_name = "Foundry User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# ---- Entra app registration for sign-in (optional) -------------------------

resource "azuread_application" "signin" {
  count            = var.require_signin ? 1 : 0
  display_name     = "Returns Desk board"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azurerm_client_config.current.object_id]

  web {
    redirect_uris = ["https://${local.app_fqdn}/.auth/login/aad/callback"]
    implicit_grant {
      id_token_issuance_enabled = true
    }
  }
}

resource "azuread_service_principal" "signin" {
  count     = var.require_signin ? 1 : 0
  client_id = azuread_application.signin[0].client_id
  owners    = [data.azurerm_client_config.current.object_id]
  # Only principals assigned below can sign in, not everyone in the tenant.
  app_role_assignment_required = true
}

# Default access (the all-zero role id) for each allowed principal; the identity running the
# apply when none are listed.
resource "azuread_app_role_assignment" "signin" {
  for_each            = var.require_signin ? toset(coalescelist(var.signin_principal_ids, [data.azurerm_client_config.current.object_id])) : toset([])
  app_role_id         = "00000000-0000-0000-0000-000000000000"
  principal_object_id = each.value
  resource_object_id  = azuread_service_principal.signin[0].object_id
}

resource "azuread_application_password" "signin" {
  count          = var.require_signin ? 1 : 0
  application_id = azuread_application.signin[0].id
  display_name   = "container-apps-easy-auth"
  end_date       = "2027-04-30T00:00:00Z" # rotate before then; a fixed date keeps the plan stable
}

# ---- the container app ------------------------------------------------------

resource "azurerm_container_app" "board" {
  name                         = local.app_name
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = data.azurerm_resource_group.rg.name
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = var.require_signin ? [1] : []
    content {
      name  = "aad-client-secret"
      value = azuread_application_password.signin[0].value
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "http"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    # The session lives in memory, so exactly one replica.
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "board"
      image  = "${azurerm_container_registry.acr.login_server}/returns-desk:${var.image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "RETURNS_DESK_ENDPOINT"
        value = local.foundry_endpoint
      }
      env {
        name  = "RETURNS_DESK_DEPLOYMENT"
        value = var.deployment_name
      }
      env {
        name  = "RETURNS_DESK_MODEL"
        value = var.model_name
      }
      # DefaultAzureCredential picks the user-assigned identity by this id.
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.app.client_id
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/healthz"
        port      = 8080
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.app_acr_pull,
    azurerm_role_assignment.app_foundry_user,
  ]
}

# Container Apps built-in authentication: redirect everyone to Entra sign-in,
# except the health probe path. azurerm has no resource for this yet, so azapi.
resource "azapi_resource" "signin" {
  count     = var.require_signin ? 1 : 0
  type      = "Microsoft.App/containerApps/authConfigs@2024-03-01"
  name      = "current"
  parent_id = azurerm_container_app.board.id

  body = {
    properties = {
      platform = { enabled = true }
      globalValidation = {
        unauthenticatedClientAction = "RedirectToLoginPage"
        redirectToProvider          = "azureactivedirectory"
        excludedPaths               = ["/healthz"]
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId                = azuread_application.signin[0].client_id
            clientSecretSettingName = "aad-client-secret"
            openIdIssuer            = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0"
          }
          validation = {
            allowedAudiences = [azuread_application.signin[0].client_id]
          }
        }
      }
      login = { preserveUrlFragmentsForLogins = false }
    }
  }
}

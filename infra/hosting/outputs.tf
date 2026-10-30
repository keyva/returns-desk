output "board_url" {
  value = "https://${azurerm_container_app.board.ingress[0].fqdn}/"
}

output "registry" {
  value = azurerm_container_registry.acr.name
}

output "build_command" {
  description = "Build and push the image in the cloud (no local Docker). Run from the repo root."
  value       = "az acr build --registry ${azurerm_container_registry.acr.name} --image returns-desk:${var.image_tag} app/"
}

output "app_identity_client_id" {
  value = azurerm_user_assigned_identity.app.client_id
}

output "signin_app_client_id" {
  value = var.require_signin ? azuread_application.signin[0].client_id : null
}

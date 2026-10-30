output "resource_group_name" {
  value = azurerm_resource_group.rg.name
}

output "foundry_account_name" {
  value = azurerm_cognitive_account.foundry.name
}

output "foundry_endpoint" {
  description = "Endpoint the OpenAI SDK expects (AzureOpenAI azure_endpoint)."
  value       = "https://${azurerm_cognitive_account.foundry.custom_subdomain_name}.openai.azure.com/"
}

output "deployment_name" {
  value = azurerm_cognitive_deployment.chat.name
}

output "project_id" {
  value = azurerm_cognitive_account_project.returns_desk.id
}

output "api_key_command" {
  description = "How to fetch a key when local_auth_enabled = true. The key itself is never put in state or output."
  value       = var.local_auth_enabled ? "az cognitiveservices account keys list -g ${azurerm_resource_group.rg.name} -n ${azurerm_cognitive_account.foundry.name} --query key1 -o tsv" : "local auth is disabled; set local_auth_enabled = true to use keys"
}

output "app_env" {
  description = "Paste into your shell before running the app."
  value       = <<-EOT
    export RETURNS_DESK_ENDPOINT=https://${azurerm_cognitive_account.foundry.custom_subdomain_name}.openai.azure.com/
    export RETURNS_DESK_DEPLOYMENT=${azurerm_cognitive_deployment.chat.name}
    export RETURNS_DESK_MODEL=${var.model_name}
  EOT
}

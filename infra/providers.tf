provider "azurerm" {
  subscription_id = var.subscription_id

  # azurerm 5.x no longer auto-registers resource providers. The subscription
  # must already have Microsoft.CognitiveServices registered:
  #   az provider register --namespace Microsoft.CognitiveServices
  features {}
}

variable "environment" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }

resource "azurerm_cognitive_account" "openai" {
  name                = "mob-openai-${var.environment}"
  resource_group_name = var.resource_group_name
  location             = var.location
  kind                 = "OpenAI"
  sku_name             = "S0"
}

resource "azurerm_cognitive_deployment" "chat_model" {
  name                 = "mob-gpt-4o-mini"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "gpt-4o-mini"
    version = "2024-07-18"
  }

  scale {
    type     = "Standard"
    capacity = 10
  }
}

output "endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "primary_key" {
  value     = azurerm_cognitive_account.openai.primary_access_key
  sensitive = true
}

output "deployment_name" {
  value = azurerm_cognitive_deployment.chat_model.name
}

resource "azurerm_service_plan" "chatbot_plan" {
  name                = "mob-chatbot-plan-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = var.environment == "prod" ? "S1" : "B1"
}

resource "azurerm_linux_web_app" "chatbot_app" {
  name                = "mob-chatbot-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location
  service_plan_id     = azurerm_service_plan.chatbot_plan.id

  site_config {
    application_stack {
      python_version = "3.11"
    }
    always_on = var.environment == "prod" ? true : false
  }

  app_settings = {
    "SCM_DO_BUILD_DURING_DEPLOYMENT" = "true"
    "ENVIRONMENT"                    = var.environment
    "SQL_CONNECTION_STRING"          = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/sql-connection-string/)"
    "OPENAI_API_KEY"                 = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/openai-api-key/)"
    "AI_SEARCH_ENDPOINT"             = var.ai_search_endpoint
    "AI_SEARCH_INDEX_NAME"           = var.ai_search_index_name
  }

  identity {
    type = "SystemAssigned"
  }

  virtual_network_subnet_id = var.app_integration_subnet_id
}

# Every Function App needs its own storage account for triggers/state -
# separate from your training-docs storage account
resource "azurerm_storage_account" "func_storage" {
  name                     = "mobfuncstor${var.environment}"
  resource_group_name     = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_service_plan" "func_plan" {
  name                = "mob-func-plan-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = var.environment == "prod" ? "EP1" : "B1" # B1 = basic
}

resource "azurerm_linux_function_app" "mob_functions" {
  name                       = "mob-functions-${var.environment}"
  resource_group_name       = var.resource_group_name
  location                   = var.location
  service_plan_id            = azurerm_service_plan.func_plan.id
  storage_account_name       = azurerm_storage_account.func_storage.name
  storage_account_access_key = azurerm_storage_account.func_storage.primary_access_key

  site_config {
    application_stack {
      python_version = "3.11"
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME" = "python"
    "ENVIRONMENT"              = var.environment
    "SQL_CONNECTION_STRING"    = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/sql-connection-string/)"
    "RESEND_API_KEY"           = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/resend-api-key/)"
    "RESEND_FROM_ADDRESS"      = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/resend-from-address/)"
  }

  identity {
    type = "SystemAssigned"
  }

  virtual_network_subnet_id = var.app_integration_subnet_id
}

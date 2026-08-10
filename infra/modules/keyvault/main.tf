data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "mob_kv" {
  name                       = "mob-kv-${var.environment}"
  resource_group_name        = var.resource_group_name
  location                   = var.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = var.environment == "prod" ? true : false
  soft_delete_retention_days = 7

  # Access is granted per-identity below via azurerm_key_vault_access_policy,
  # not a broad access policy here - keeps this least-privilege by default.
}

# Lets the Terraform/pipeline identity write secrets during apply
resource "azurerm_key_vault_access_policy" "pipeline_identity" {
  key_vault_id = azurerm_key_vault.mob_kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = var.pipeline_identity_object_id

  secret_permissions = ["Get", "List", "Set", "Delete"]
}

# The actual secrets - values are passed in as sensitive variables, never
# hardcoded here or committed to the repo
resource "azurerm_key_vault_secret" "sql_connection_string" {
  name         = "sql-connection-string"
  value        = var.sql_connection_string
  key_vault_id = azurerm_key_vault.mob_kv.id
}

resource "azurerm_key_vault_secret" "openai_api_key" {
  name         = "openai-api-key"
  value        = var.openai_api_key
  key_vault_id = azurerm_key_vault.mob_kv.id
}

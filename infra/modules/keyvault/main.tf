data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "mob_kv" {
  name                       = "mob-kv-${var.environment}"
  resource_group_name        = var.resource_group_name
  location                   = var.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = var.environment == "prod" ? true : false
  soft_delete_retention_days = 7
}

resource "azurerm_key_vault_access_policy" "pipeline_identity" {
  key_vault_id = azurerm_key_vault.mob_kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = var.pipeline_identity_object_id

  secret_permissions = ["Get", "List", "Set", "Delete"]
}

# Lets the Function App itself read secrets at runtime - without this,
# its @Microsoft.KeyVault(...) references silently resolve to empty.
resource "azurerm_key_vault_access_policy" "function_app_identity" {
  key_vault_id = azurerm_key_vault.mob_kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = var.function_app_principal_id

  secret_permissions = ["Get", "List"]
}

# Local development access for team members' own accounts
resource "azurerm_key_vault_access_policy" "local_dev" {
  for_each     = toset(var.local_dev_object_ids)
  key_vault_id = azurerm_key_vault.mob_kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = each.value

  secret_permissions = ["Get", "List", "Set", "Delete"]
}

resource "azurerm_key_vault_secret" "sql_connection_string" {
  name         = "sql-connection-string"
  value        = var.sql_connection_string
  key_vault_id = azurerm_key_vault.mob_kv.id
}

resource "azurerm_key_vault_secret" "openai_api_key" {
  count        = var.openai_api_key != "" ? 1 : 0
  name         = "openai-api-key"
  value        = var.openai_api_key
  key_vault_id = azurerm_key_vault.mob_kv.id
}

resource "azurerm_key_vault_secret" "sql_password" {
  name         = "sql-password"
  value        = var.sql_admin_password
  key_vault_id = azurerm_key_vault.mob_kv.id
}

resource "azurerm_key_vault_secret" "jwt_signing_secret" {
  name         = "jwt-signing-secret"
  value        = var.jwt_signing_secret
  key_vault_id = azurerm_key_vault.mob_kv.id
}

resource "azurerm_key_vault_secret" "doc_intelligence_key" {
  count        = var.doc_intelligence_key != "" ? 1 : 0
  name         = "doc-intelligence-key"
  value        = var.doc_intelligence_key
  key_vault_id = azurerm_key_vault.mob_kv.id
}

resource "azurerm_key_vault_secret" "storage_connection_string" {
  name         = "storage-connection-string"
  value        = var.storage_connection_string
  key_vault_id = azurerm_key_vault.mob_kv.id
}

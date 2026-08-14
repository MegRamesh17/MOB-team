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

# NOTE: the Function App's own access policy used to live here, but that
# created a circular module dependency -- this module needed the Function
# App's principal_id (module.functions.function_app_identity_principal_id),
# while the functions module needs this module's key_vault_uri. Terraform
# can't resolve module.keyvault -> module.functions -> module.keyvault.
# That policy now lives in the ROOT infra/main.tf instead, declared after
# both modules exist, which breaks the cycle. See infra/main.tf.

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

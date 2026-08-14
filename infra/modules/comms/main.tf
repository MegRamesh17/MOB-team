resource "azurerm_key_vault_secret" "resend_api_key" {
  name         = "resend-api-key"
  value        = var.resend_api_key
  key_vault_id = var.key_vault_id
}

resource "azurerm_key_vault_secret" "resend_from_address" {
  name         = "resend-from-address"
  value        = var.resend_from_address
  key_vault_id = var.key_vault_id
}

output "resend_api_key_secret_name" {
  value = azurerm_key_vault_secret.resend_api_key.name
}

output "resend_from_address_secret_name" {
  value = azurerm_key_vault_secret.resend_from_address.name
}

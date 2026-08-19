output "resend_api_key_secret_name" {
  # resend_api_key is count-based (0 or 1) -- empty string, not an index-out-of-range
  # error, when it wasn't configured for this apply.
  value = try(azurerm_key_vault_secret.resend_api_key[0].name, "")
}

output "resend_from_address_secret_name" {
  value = azurerm_key_vault_secret.resend_from_address.name
}

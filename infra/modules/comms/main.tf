# Resend, not Azure Communication Services. ACS needed the Microsoft.Communication
# resource provider registered on the subscription, which this account does not have
# permission to do, and no admin request had gone through. Resend is a third-party API,
# so there is no Azure resource to provision at all -- this module's only job is putting
# the two secrets the Function App needs into Key Vault, the same way sql-password and
# jwt-signing-secret already get there.

resource "azurerm_key_vault_secret" "resend_api_key" {
  # Empty by default (see infra/variables.tf's resend_api_key) until someone adds it as
  # a GitHub secret. Conditional the same way openai_api_key/doc_intelligence_key are in
  # infra/modules/keyvault/main.tf, so an unconfigured deploy doesn't try to write an
  # empty secret value.
  count        = var.resend_api_key != "" ? 1 : 0
  name         = "resend-api-key"
  value        = var.resend_api_key
  key_vault_id = var.key_vault_id
}

resource "azurerm_key_vault_secret" "resend_from_address" {
  name         = "resend-from-address"
  value        = var.resend_from_address
  key_vault_id = var.key_vault_id
}

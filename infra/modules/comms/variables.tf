variable "environment" {
  type = string
}

variable "key_vault_id" {
  description = "Resource ID of the Key Vault (module.keyvault.key_vault_id) to store the Resend secrets in."
  type        = string
}

variable "resend_api_key" {
  description = "Resend API key from resend.com. Passed in via -var in CI, same as sql_admin_password."
  type        = string
  sensitive   = true
}

variable "resend_from_address" {
  description = "Verified 'from' address in Resend, shared across all tenants."
  type        = string
  default     = "onboarding@resend.dev" # swap once a real domain is verified in Resend
}

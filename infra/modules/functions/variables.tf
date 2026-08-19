variable "environment" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type    = string
  default = "East US"
}

variable "key_vault_uri" {
  type = string
}

variable "app_integration_subnet_id" {
  type = string
}

variable "sql_server_fqdn" { type = string }
variable "sql_database_name" { type = string }
variable "sql_admin_username" {
  type    = string
  default = "mobsqladmin"
}


variable "doc_intelligence_endpoint" {
  type    = string
  default = ""
}

variable "azure_openai_endpoint" {
  type    = string
  default = ""
}

variable "quizgen_provider" {
  type    = string
  default = "mock"
}

variable "resend_api_key" {
  # Only checked for emptiness here (== "" gates the RESEND_API_KEY app setting below,
  # same pattern as doc_intelligence_endpoint) -- the value itself lives in Key Vault,
  # written by module.comms, and is never read through this module.
  type      = string
  sensitive = true
  default   = ""
}

variable "allowed_origins" {
  description = "Exact browser origins allowed to call the Function App"
  type        = list(string)
  default     = []
}

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

variable "comms_connection_string" {
  type      = string
  sensitive = true
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

variable "allowed_origins" {
  description = "Exact browser origins allowed to call the Function App"
  type        = list(string)
  default     = []
}

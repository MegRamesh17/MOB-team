variable "sql_admin_password" {
  description = "Admin password for the Azure SQL server"
  type        = string
  sensitive   = true
}

variable "pipeline_identity_object_id" {
  description = "Object ID of the service principal GitHub Actions uses to authenticate"
  type        = string
}

variable "openai_api_key" {
  type      = string
  sensitive = true
}

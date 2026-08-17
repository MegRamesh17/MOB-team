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

variable "pipeline_identity_object_id" {
  description = "Object ID of the service principal / managed identity the pipeline runs as"
  type        = string
}

variable "sql_connection_string" {
  type      = string
  sensitive = true
}

variable "openai_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "function_app_principal_id" {
  description = "Principal ID of the Function App's managed identity"
  type        = string
}

variable "local_dev_object_ids" {
  description = "Object IDs of team members who need local Key Vault access"
  type        = list(string)
  default     = []
}

variable "sql_admin_password" {
  type      = string
  sensitive = true
}

variable "jwt_signing_secret" {
  description = "HS256 signing key for session tokens issued by api/shared/auth.py"
  type        = string
  sensitive   = true
}

variable "doc_intelligence_key" {
  description = "Azure Document Intelligence key. Empty until the resource is wired up."
  type        = string
  sensitive   = true
  default     = ""
}

variable "storage_connection_string" {
  type      = string
  sensitive = true
}

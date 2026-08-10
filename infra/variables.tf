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

variable "environment" {
  description = "Environment name, used for resource naming (e.g. dev, prod)"
  type        = string
  default     = "dev"
}

variable "resource_group_name" {
  description = "Name of the existing Azure resource group"
  type        = string
  default     = "MOB"
}

variable "location" {
  description = "Azure region for the new modules"
  type        = string
  default     = "eastus"
}
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
  default   = ""
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

variable "local_dev_object_ids" {
  description = "Object IDs of team members who need local Key Vault access"
  type        = list(string)
  default     = []
}


variable "doc_intelligence_key" {
  description = "Azure Document Intelligence key (portal-created resource)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "doc_intelligence_endpoint" {
  description = "Azure Document Intelligence endpoint, e.g. https://<name>.cognitiveservices.azure.com/"
  type        = string
  default     = ""
}

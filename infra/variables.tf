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

variable "additional_frontend_origins" {
  description = "Additional exact frontend origins, such as a future custom domain"
  type        = list(string)
  default     = []
}

variable "azure_openai_endpoint" {
  description = <<-EOT
    Azure OpenAI endpoint for the shared Foundry resource, e.g.
    https://sharedfoundry.services.ai.azure.com. The key itself is openai_api_key above,
    already stored in Key Vault as openai-api-key -- this was never surfaced as a Function
    App setting until now, so /documents/confirm's generation step had a key in Key Vault
    it could not actually reach.
  EOT
  type        = string
  default     = ""
}

variable "quizgen_provider" {
  description = <<-EOT
    'mock' (default, free, deterministic, no credentials needed) or 'azure' (real gpt-5,
    roughly a cent per question -- see requirements.txt's note on cost). Matches
    src/quizgen/config.py's QUIZGEN_PROVIDER, which already defaults to 'mock' if this is
    never set -- stated explicitly here so switching to the real model is one variable,
    not a portal setting nobody remembers exists.
  EOT
  type        = string
  default     = "mock"
}

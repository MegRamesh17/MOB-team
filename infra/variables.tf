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

variable "resend_api_key" {
  description = <<-EOT
    API key from resend.com, used by the daily certificate-expiry-reminder timer
    function. Empty by default, same reason openai_api_key/doc_intelligence_key are:
    terraform.yml's plan/apply don't pass it yet, so a required variable with no default
    would fail every plan on every PR until someone adds it as a GitHub secret. Empty
    means the Key Vault secret and the Function App setting are simply not created --
    see infra/modules/comms and RESEND_API_KEY below -- and the timer function logs and
    skips rather than crashing.
  EOT
  type        = string
  sensitive   = true
  default     = ""
}

variable "quizgen_provider" {
  description = <<-EOT
    'mock' (free, deterministic, no credentials needed) or 'azure' (real gpt-5, roughly a
    cent per question -- see requirements.txt's note on cost). Matches
    src/quizgen/config.py's QUIZGEN_PROVIDER.

    Defaults to 'azure' now that OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT are set as
    GitHub secrets and wired through terraform.yml -- CONFIG.require_azure() fails the
    generation request loudly (503, "Role mapping needs the real model") rather than
    silently falling back to mock if either secret is ever unset, so this is safe to
    default on rather than something that quietly degrades.
  EOT
  type        = string
  default     = "azure"
}

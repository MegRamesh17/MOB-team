variable "environment" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "swa_location" {
  description = "Azure region for the Static Web App. Must be a region SWA is actually offered in (centralus, eastus2, eastasia, westeurope, westus2) -- most Azure regions, including eastus, are not supported."
  type        = string
  default     = "eastus2"
}

variable "sku_tier" {
  description = "Free or Standard. Standard is needed for custom auth providers / more than one deployment environment; Free is enough for a single dev site."
  type        = string
  default     = "Free"
}

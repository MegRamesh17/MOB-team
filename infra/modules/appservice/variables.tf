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

variable "ai_search_endpoint" {
  type    = string
  default = ""
}

variable "ai_search_index_name" {
  type    = string
  default = "training-docs-index"
}

variable "app_integration_subnet_id" {
  type = string
}

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

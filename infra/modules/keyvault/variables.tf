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
}

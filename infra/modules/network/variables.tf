variable "environment" {
  description = "dev, staging, or prod"
  type        = string
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type    = string
  default = "East US"
}

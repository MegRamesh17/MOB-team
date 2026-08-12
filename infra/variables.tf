variable "sql_admin_password" {
  description = "Admin password for the Azure SQL server"
  type        = string
  sensitive   = true
}

variable "environment" {
  description = "Environment name, used for resource naming"
  type        = string
  default     = "dev"
}

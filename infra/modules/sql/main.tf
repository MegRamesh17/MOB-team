variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "admin_username" {
  type    = string
  default = "mobsqladmin"
}
variable "admin_password" {
  type      = string
  sensitive = true
}

resource "azurerm_mssql_server" "sql" {
  name                         = "mob-sql-server-02"
  resource_group_name          = var.resource_group_name
  location                     = var.location
  version                      = "12.0"
  administrator_login          = var.admin_username
  administrator_login_password = var.admin_password
}

resource "azurerm_mssql_database" "db" {
  name        = "mob-training-db"
  server_id   = azurerm_mssql_server.sql.id
  sku_name    = "Basic"
  max_size_gb = 2
}

resource "azurerm_mssql_firewall_rule" "allow_azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_mssql_server.sql.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

output "server_fqdn" {
  value = azurerm_mssql_server.sql.fully_qualified_domain_name
}

output "database_name" {
  value = azurerm_mssql_database.db.name
}

output "connection_string" {
  value     = "Driver={ODBC Driver 18 for SQL Server};Server=tcp:${azurerm_mssql_server.sql.fully_qualified_domain_name},1433;Database=${azurerm_mssql_database.db.name};Uid=${var.admin_username};Pwd=${var.admin_password};Encrypt=yes;"
  sensitive = true
}

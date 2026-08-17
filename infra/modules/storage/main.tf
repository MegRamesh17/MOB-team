variable "resource_group_name" { type = string }
variable "location" { type = string }

resource "azurerm_storage_account" "mob_storage" {
  name                     = "mobtrainingstorage"
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_storage_container" "software_engineering_docs" {
  name                  = "software-engineering-docs"
  storage_account_name  = azurerm_storage_account.mob_storage.name
  container_access_type = "private"
}

resource "azurerm_storage_container" "company_docs" {
  name                  = "company-docs"
  storage_account_name  = azurerm_storage_account.mob_storage.name
  container_access_type = "private"
}

output "storage_account_name" {
  value = azurerm_storage_account.mob_storage.name
}

output "storage_account_id" {
  value = azurerm_storage_account.mob_storage.id
}

resource "azurerm_storage_container" "certificates" {
  name                  = "certificates"
  storage_account_name  = azurerm_storage_account.mob_storage.name
  container_access_type = "private"
}

output "primary_connection_string" {
  value     = azurerm_storage_account.mob_storage.primary_connection_string
  sensitive = true
}

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}

data "azurerm_resource_group" "mob_rg" {
  name = "MOB"
}

resource "azurerm_storage_account" "mob_storage" {
  name                     = "mobtrainingstorage" 
  resource_group_name      = data.azurerm_resource_group.mob_rg.name
  location                 = data.azurerm_resource_group.mob_rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_storage_container" "training_docs" {
  name                  = "training-docs"
  storage_account_name  = azurerm_storage_account.mob_storage.name
  container_access_type = "private"
}
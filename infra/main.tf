terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
  backend "azurerm" {
    resource_group_name  = "MOB"
    storage_account_name = "mobtfstate"
    container_name       = "tfstate"
    key                  = "terraform.tfstate"
  }
}

provider "azurerm" {
  features {}
}

data "azurerm_resource_group" "mob_rg" {
  name = "MOB"
}

module "storage" {
  source              = "./modules/storage"
  resource_group_name = data.azurerm_resource_group.mob_rg.name
  location             = data.azurerm_resource_group.mob_rg.location
}

module "sql" {
  source              = "./modules/sql"
  resource_group_name = data.azurerm_resource_group.mob_rg.name
  location             = "southcentralus"
  admin_password       = var.sql_admin_password
}

module "comms" {
  source              = "./modules/comms"
  environment         = var.environment
  resource_group_name = data.azurerm_resource_group.mob_rg.name
}
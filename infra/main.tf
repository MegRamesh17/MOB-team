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

module "network" {
  source              = "./modules/network"
  environment         = var.environment
  resource_group_name = var.resource_group_name
  location            = var.location
}

module "keyvault" {
  source                       = "./modules/keyvault"
  environment                  = var.environment
  resource_group_name          = var.resource_group_name
  location                     = var.location
  pipeline_identity_object_id  = var.pipeline_identity_object_id
  sql_connection_string        = module.sql.connection_string
  openai_api_key                = var.openai_api_key
}

# TEMPORARILY DISABLED: blocked on Microsoft.Communication provider
# registration - subscription lacks permission, admin request pending.
# Re-enable once registered; also switch functions' comms_connection_string
# back to module.comms.comms_connection_string when this comes back.
#
# module "comms" {
#   source              = "./modules/comms"
#   environment         = var.environment
#   resource_group_name = var.resource_group_name
# }

module "functions" {
  source                     = "./modules/functions"
  environment                = var.environment
  resource_group_name        = var.resource_group_name
  location                   = var.location
  key_vault_uri               = module.keyvault.key_vault_uri
  comms_connection_string    = "placeholder-until-comms-unblocked"
  app_integration_subnet_id  = module.network.app_integration_subnet_id
}

module "appservice" {
  source                     = "./modules/appservice"
  environment                = var.environment
  resource_group_name        = var.resource_group_name
  location                   = var.location
  key_vault_uri               = module.keyvault.key_vault_uri
  app_integration_subnet_id  = module.network.app_integration_subnet_id
}

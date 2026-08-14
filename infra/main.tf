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
data "azurerm_client_config" "current" {}
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
  local_dev_object_ids         = var.local_dev_object_ids
}

# Switched from Azure Communication Services to Resend -- ACS was blocked on
# Microsoft.Communication provider registration (no subscription permission
# to register it). Resend is a third-party API, so this module just writes
# secrets into the existing Key Vault; no provider registration needed.
module "comms" {
  source          = "./modules/comms"
  environment     = var.environment
  key_vault_id    = module.keyvault.key_vault_id
  resend_api_key  = var.resend_api_key
}

module "functions" {
  source                     = "./modules/functions"
  environment                = var.environment
  resource_group_name        = var.resource_group_name
  location                   = var.location
  key_vault_uri               = module.keyvault.key_vault_uri
  app_integration_subnet_id  = module.network.app_integration_subnet_id

  depends_on = [module.comms]
}

# Grants the Function App's own managed identity read-only access to Key
# Vault secrets, so its @Microsoft.KeyVault(...) app settings actually
# resolve at runtime. Declared HERE at the root -- not inside the keyvault
# module -- because keyvault needing functions' principal_id while functions
# needs keyvault's URI is a circular module dependency. Putting it here,
# after both modules already exist, breaks that cycle.
#
# Scoped to "Get" only (read-only). Compare to pipeline_identity in the
# keyvault module, which has Get/List/Set/Delete because it *provisions*
# secrets during terraform apply -- this identity only ever *reads* one
# secret value at a time and should never list, create, or delete anything.
resource "azurerm_key_vault_access_policy" "function_app_identity" {
  key_vault_id = module.keyvault.key_vault_id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = module.functions.function_app_identity_principal_id

  secret_permissions = ["Get"]
}

module "appservice" {
  source                     = "./modules/appservice"
  environment                = var.environment
  resource_group_name        = var.resource_group_name
  location                   = var.location
  key_vault_uri               = module.keyvault.key_vault_uri
  app_integration_subnet_id  = module.network.app_integration_subnet_id
}

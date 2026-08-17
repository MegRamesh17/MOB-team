terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
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

# Signing key for session tokens. Generated once and held in state rather than
# passed in as a variable, so there is no manual step and nobody has to keep a
# copy of it anywhere.
#
# Stability matters more than it looks: this key signs every live session, so
# regenerating it on an apply would sign every user out. `random_password` only
# regenerates when its keepers change, and there are none.
resource "random_password" "jwt_signing_secret" {
  length  = 64
  special = false
}
data "azurerm_resource_group" "mob_rg" {
  name = "MOB"
}
module "storage" {
  source              = "./modules/storage"
  resource_group_name = data.azurerm_resource_group.mob_rg.name
  location            = data.azurerm_resource_group.mob_rg.location
}
module "sql" {
  source              = "./modules/sql"
  resource_group_name = data.azurerm_resource_group.mob_rg.name
  location            = "southcentralus"
  admin_password      = var.sql_admin_password
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
  storage_connection_string    = module.storage.primary_connection_string
  sql_admin_password            = var.sql_admin_password
  openai_api_key                = var.openai_api_key
  function_app_principal_id    = module.functions.function_app_identity_principal_id
  local_dev_object_ids         = var.local_dev_object_ids
  jwt_signing_secret           = random_password.jwt_signing_secret.result
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
  source                    = "./modules/functions"
  environment               = var.environment
  resource_group_name       = var.resource_group_name
  location                  = var.location
  key_vault_uri             = module.keyvault.key_vault_uri
  comms_connection_string   = "placeholder-until-comms-unblocked"
  app_integration_subnet_id = module.network.app_integration_subnet_id
  sql_server_fqdn           = module.sql.server_fqdn
  sql_database_name         = module.sql.database_name
}
module "appservice" {
  source                    = "./modules/appservice"
  environment               = var.environment
  resource_group_name       = var.resource_group_name
  location                  = var.location
  key_vault_uri             = module.keyvault.key_vault_uri
  app_integration_subnet_id = module.network.app_integration_subnet_id
}

module "staticwebapp" {
  source               = "./modules/staticwebapp"
  environment          = var.environment
  resource_group_name  = data.azurerm_resource_group.mob_rg.name
  # No /api suffix: web-app/src/api.js does BASE + "/api/login" etc., so BASE
  # itself is just the host -- an /api suffix here would double up to .../api/api/....
  api_base_url         = "https://${module.functions.function_app_name}.azurewebsites.net"
}

# Read by the Azure DevOps frontend pipeline to authenticate its deploy
# (`az staticwebapp` / the SWA deploy task takes this as a bearer token). Not
# wired into Key Vault: unlike the app secrets above, nothing at runtime reads
# this -- only a human copying it into a DevOps pipeline variable once needs it,
# via `terraform output -raw swa_deploy_token`.
output "swa_deploy_token" {
  value     = module.staticwebapp.api_key
  sensitive = true
}

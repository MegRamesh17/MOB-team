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
  source                      = "./modules/keyvault"
  environment                 = var.environment
  resource_group_name         = var.resource_group_name
  location                    = var.location
  pipeline_identity_object_id = var.pipeline_identity_object_id
  sql_connection_string       = module.sql.connection_string
  storage_connection_string   = module.storage.primary_connection_string
  sql_admin_password          = var.sql_admin_password
  openai_api_key              = var.openai_api_key
  # Same gap as doc_intelligence_endpoint below: declared at root, matched by a
  # variable on this module, never actually connected -- so the doc-intelligence-key
  # secret this module can create was never created by an automated apply.
  doc_intelligence_key      = var.doc_intelligence_key
  function_app_principal_id = module.functions.function_app_identity_principal_id
  local_dev_object_ids      = var.local_dev_object_ids
  jwt_signing_secret        = random_password.jwt_signing_secret.result
}

# Was Azure Communication Services, blocked on Microsoft.Communication provider
# registration (no subscription permission, admin request never went through).
# Switched to Resend, a third-party API with no Azure resource to register --
# this module only writes two secrets into the Key Vault that already exists.
module "comms" {
  source         = "./modules/comms"
  environment    = var.environment
  key_vault_id   = module.keyvault.key_vault_id
  resend_api_key = var.resend_api_key
}
module "functions" {
  source                    = "./modules/functions"
  environment               = var.environment
  resource_group_name       = var.resource_group_name
  location                  = var.location
  key_vault_uri             = module.keyvault.key_vault_uri
  app_integration_subnet_id = module.network.app_integration_subnet_id
  sql_server_fqdn           = module.sql.server_fqdn
  sql_database_name         = module.sql.database_name
  resend_api_key            = var.resend_api_key

  # Not a plan-time data dependency (the Function App's RESEND_* app settings are just
  # @Microsoft.KeyVault(...) reference strings, not values read from module.comms), but
  # the secrets those references resolve to at RUNTIME need to already exist by the time
  # the Function App starts, so the apply itself must not race the two.
  depends_on = [module.comms]
  allowed_origins = concat(
    ["https://${module.staticwebapp.default_host_name}"],
    var.additional_frontend_origins,
  )
  # doc_intelligence_endpoint was defined at root and never actually reached this
  # module -- the Function App's app_settings read it via var.doc_intelligence_endpoint,
  # but with nothing passed in here that always resolved to the module's own
  # empty-string default regardless of what was set at apply time. Document
  # Intelligence was silently unconfigured on every deploy as a result -- caught while
  # wiring the OpenAI key through for the first time, not something that changed just
  # now. There is no doc_intelligence_key variable on this module: the key is derived
  # entirely from the Key Vault reference below, conditioned on the endpoint being set.
  doc_intelligence_endpoint = var.doc_intelligence_endpoint
  azure_openai_endpoint     = var.azure_openai_endpoint
  quizgen_provider          = var.quizgen_provider
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
  source              = "./modules/staticwebapp"
  environment         = var.environment
  resource_group_name = data.azurerm_resource_group.mob_rg.name
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

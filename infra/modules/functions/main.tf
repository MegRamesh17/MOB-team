# Every Function App needs its own storage account for triggers/state -
# separate from your training-docs storage account
resource "azurerm_storage_account" "func_storage" {
  name                     = "mobfuncstor${var.environment}"
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_service_plan" "func_plan" {
  name                = "mob-func-plan-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = var.environment == "prod" ? "EP1" : "B1" # B1 = basic
}

resource "azurerm_linux_function_app" "mob_functions" {
  name                       = "mob-functions-${var.environment}"
  resource_group_name        = var.resource_group_name
  location                   = var.location
  service_plan_id            = azurerm_service_plan.func_plan.id
  storage_account_name       = azurerm_storage_account.func_storage.name
  storage_account_access_key = azurerm_storage_account.func_storage.primary_access_key

  site_config {
    application_stack {
      python_version = "3.11"
    }

    cors {
      allowed_origins     = var.allowed_origins
      support_credentials = false
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME" = "python"
    "ENVIRONMENT"              = var.environment
    # With a requirements.txt present, Azure's zip-deploy default can attempt its own
    # remote build (Oryx) on top of the package azure-pipelines-backend.yml already
    # built and vendored -- ambiguous at best, and a silently-failed remote build (e.g.
    # missing ODBC dev headers for pyodbc) produces exactly "host up, zero routes
    # registered" with no clear error. Pinning both settings makes Azure run only from
    # the exact zip the pipeline shipped, with no remote build in the picture at all.
    "WEBSITE_RUN_FROM_PACKAGE"       = "1"
    "SCM_DO_BUILD_DURING_DEPLOYMENT" = "false"
    "SQL_SERVER"                     = var.sql_server_fqdn
    "SQL_DATABASE"                   = var.sql_database_name
    "SQL_USER"                       = var.sql_admin_username
    "SQL_PASSWORD"                   = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/sql-password/)"
    "JWT_SIGNING_SECRET"             = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/jwt-signing-secret/)"
    # Both empty until the endpoint variable is set. Extraction falls back to
    # pypdf when they are, so the app runs either way.
    "DOCUMENT_INTELLIGENCE_ENDPOINT" = var.doc_intelligence_endpoint
    "DOCUMENT_INTELLIGENCE_KEY"      = var.doc_intelligence_endpoint == "" ? "" : "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/doc-intelligence-key/)"
    # Resend, not Azure Communication Services -- see infra/modules/comms. Empty until
    # resend_api_key is configured, same conditional pattern as Document Intelligence
    # above: module.comms's secret is count-based and simply doesn't exist yet when
    # resend_api_key is "", and a @Microsoft.KeyVault(...) reference to a secret that
    # doesn't exist would otherwise show as an unresolved app setting for no reason.
    "RESEND_API_KEY"                  = var.resend_api_key == "" ? "" : "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/resend-api-key/)"
    "RESEND_FROM_ADDRESS"             = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/resend-from-address/)"
    "EXPIRY_WARNING_DAYS"             = "30"
    "AZURE_STORAGE_CONNECTION_STRING" = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/storage-connection-string/)"
    "QUIZGEN_PASSING_SCORE"           = "80"
    "QUIZGEN_QUIZ_LENGTH"             = "8"
    # Recording-friendly generation: normal coverage, grounding, citations and lesson
    # quality, with two concurrent authors, no redundant research for substantial source
    # material, and one balanced assessment batch. False restores the sequential path.
    "QUIZGEN_DEMO_FAST" = "true"
    # Names quizgen/config.py already reads (_first("...", "AZURE_OPENAI_ENDPOINT") /
    # _first("...", "AZURE_OPENAI_KEY", ...)) -- nothing to change on the application
    # side, this was purely a missing app setting. Same conditional pattern as Document
    # Intelligence above: empty until the endpoint variable is set, so the app runs
    # either way and /documents/confirm's generation step fails loudly (503, "needs the
    # real model") rather than silently, same as it already does locally without .env.
    "AZURE_OPENAI_ENDPOINT" = var.azure_openai_endpoint
    "AZURE_OPENAI_KEY"      = var.azure_openai_endpoint == "" ? "" : "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/openai-api-key/)"
    "QUIZGEN_PROVIDER"      = var.quizgen_provider
  }

  identity {
    type = "SystemAssigned"
  }

  virtual_network_subnet_id = var.app_integration_subnet_id
}

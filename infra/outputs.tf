output "sql_server_fqdn" {
  value = module.sql.server_fqdn
}

output "sql_database_name" {
  value = module.sql.database_name
}

output "storage_account_name" {
  value = module.storage.storage_account_name
}

output "key_vault_uri" {
  value = module.keyvault.key_vault_uri
}

# comms output disabled along with the module - blocked on
# Microsoft.Communication provider registration
# output "comms_connection_string" {
#   value     = module.comms.comms_connection_string
#   sensitive = true
# }

output "function_app_name" {
  value = module.functions.function_app_name
}

output "chatbot_url" {
  value = module.appservice.chatbot_app_url
}


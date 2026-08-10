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

output "comms_connection_string" {
  value     = module.comms.comms_connection_string
  sensitive = true
}

output "function_app_hostname" {
  value = module.functions.function_app_hostname
}

output "chatbot_url" {
  value = module.appservice.chatbot_url
}

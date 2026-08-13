output "function_app_name" {
  value = azurerm_linux_function_app.mob_functions.name
}

output "function_app_identity_principal_id" {
  value = azurerm_linux_function_app.mob_functions.identity[0].principal_id
}

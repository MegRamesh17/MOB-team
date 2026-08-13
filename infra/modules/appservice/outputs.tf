output "chatbot_app_name" {
  value = azurerm_linux_web_app.chatbot_app.name
}

output "chatbot_app_url" {
  value = "https://${azurerm_linux_web_app.chatbot_app.default_hostname}"
}

output "chatbot_identity_principal_id" {
  value = azurerm_linux_web_app.chatbot_app.identity[0].principal_id
}

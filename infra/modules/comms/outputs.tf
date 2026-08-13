output "comms_connection_string" {
  value     = azurerm_communication_service.mob_comms.primary_connection_string
  sensitive = true
}

output "sender_domain" {
  value = azurerm_email_communication_service_domain.mob_email_domain.mail_from_sender_domain
}

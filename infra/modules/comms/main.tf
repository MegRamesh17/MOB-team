resource "azurerm_communication_service" "mob_comms" {
  name                = "mob-comms-${var.environment}"
  resource_group_name = var.resource_group_name
  data_location       = "United States"
}

# Managed email domain (Azure-provided, no custom DNS setup needed to start)
resource "azurerm_email_communication_service" "mob_email" {
  name                = "mob-email-${var.environment}"
  resource_group_name = var.resource_group_name
  data_location       = "United States"
}

resource "azurerm_email_communication_service_domain" "mob_email_domain" {
  name              = "AzureManagedDomain"
  email_service_id  = azurerm_email_communication_service.mob_email.id
  domain_management = "AzureManaged"
}

# Links the email domain to the Communication Service so the Function can
# send mail using this service's connection string
resource "azurerm_communication_service_email_domain_association" "link" {
  communication_service_id = azurerm_communication_service.mob_comms.id
  email_service_domain_id  = azurerm_email_communication_service_domain.mob_email_domain.id
}

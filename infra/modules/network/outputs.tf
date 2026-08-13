output "vnet_id" {
  value = azurerm_virtual_network.mob_vnet.id
}

output "app_integration_subnet_id" {
  value = azurerm_subnet.app_integration.id
}

output "private_endpoints_subnet_id" {
  value = azurerm_subnet.private_endpoints.id
}

resource "azurerm_virtual_network" "mob_vnet" {
  name                = "mob-vnet-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location
  address_space       = ["10.10.0.0/16"]
}

# Subnet the App Service / Function App delegate into for outbound VNet integration
resource "azurerm_subnet" "app_integration" {
  name                 = "mob-app-integration-${var.environment}"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.mob_vnet.name
  address_prefixes     = ["10.10.1.0/24"]

  delegation {
    name = "app-service-delegation"
    service_delegation {
      name    = "Microsoft.Web/serverFarms"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

# Subnet reserved for private endpoints (SQL, Key Vault, Storage) so traffic
# doesn't leave the VNet for internal calls
resource "azurerm_subnet" "private_endpoints" {
  name                 = "mob-private-endpoints-${var.environment}"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.mob_vnet.name
  address_prefixes     = ["10.10.2.0/24"]

  private_endpoint_network_policies = "Disabled"
}

resource "azurerm_network_security_group" "app_nsg" {
  name                = "mob-app-nsg-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location

  security_rule {
    name                       = "AllowHTTPSInbound"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "app_nsg_assoc" {
  subnet_id                 = azurerm_subnet.app_integration.id
  network_security_group_id = azurerm_network_security_group.app_nsg.id
}

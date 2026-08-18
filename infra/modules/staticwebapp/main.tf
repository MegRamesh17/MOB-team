resource "azurerm_static_web_app" "frontend" {
  name                = "mob-frontend-${var.environment}"
  resource_group_name = var.resource_group_name
  # Static Web Apps is only available in a handful of regions (centralus, eastus2,
  # eastasia, westeurope, westus2 as of writing) -- NOT eastus, which is what
  # infra/variables.tf's shared `location` default is. Deliberately its own
  # variable rather than reusing var.location so a shared-location change elsewhere
  # can't silently break this.
  location = var.swa_location
  sku_tier = var.sku_tier
  sku_size = var.sku_tier

  # Deliberately no app_settings here. SWA app settings are only visible to a
  # managed Functions API attached to this resource. VITE_API_BASE is instead
  # baked into the static bundle by azure-pipelines-frontend.yml.
}

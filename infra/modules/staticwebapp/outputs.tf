output "name" {
  value = azurerm_static_web_app.frontend.name
}

# What the frontend pipeline should bake in as VITE_API_BASE at build time (Vite
# env vars are compile-time only -- see main.tf's comment on why this isn't an
# app_setting on the resource itself).
output "api_base_url" {
  value = var.api_base_url
}

output "default_host_name" {
  value = azurerm_static_web_app.frontend.default_host_name
}

# The DevOps frontend pipeline needs this to authenticate the deploy (the
# AzureStaticWebApp task takes it as azure_static_web_apps_api_token). Sensitive
# because it is a bearer credential for pushing content to the site -- store it in
# a DevOps secret variable / variable group, never print it in a pipeline log.
output "api_key" {
  value     = azurerm_static_web_app.frontend.api_key
  sensitive = true
}

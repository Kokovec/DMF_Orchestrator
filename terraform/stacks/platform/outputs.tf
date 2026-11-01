output "base_domain" {
  value = var.base_domain
}

output "mxl_root" {
  value = "/var/lib/dmf/mxl"
}

output "keycloak" {
  value = {
    public_url   = local.keycloak_public
    internal_url = local.keycloak_internal
    admin_url    = "https://${local.keycloak_host}"
    host         = local.keycloak_host
  }
}

output "keycloak_admin_password" {
  value     = random_password.keycloak_admin.result
  sensitive = true
}

output "operator_passwords" {
  description = "Initial passwords of the demo operators (realm dmf)."
  value       = { for u in var.operators : u => random_password.operator[u].result }
  sensitive   = true
}

output "grafana" {
  value = {
    url            = "https://${local.grafana_host}"
    admin_password = random_password.grafana_admin.result
  }
  sensitive = true
}

output "nmos_registry_url" {
  value = "http://nmos-registry.${local.system_ns}.svc.cluster.local:8010"
}

output "root_ca_pem" {
  description = "Root CA of the local PKI; import into browsers with `make trust-ca`."
  # The certificate is public; only the key in the same Secret is sensitive.
  value = nonsensitive(data.kubernetes_secret_v1.root_ca.data["ca.crt"])
}

output "platform_hostnames" {
  value = [local.keycloak_host, local.grafana_host]
}

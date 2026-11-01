locals {
  namespaces = {
    "dmf-system"     = { layer = "media-exchange", psa = "restricted" } # NMOS registry
    "dmf-identity"   = { layer = "application-ui", psa = "restricted" } # Keycloak
    "dmf-monitoring" = { layer = "monitoring", psa = "privileged" }     # node-exporter needs host access
    "cert-manager"   = { layer = "security", psa = "restricted" }
  }
}

resource "kubernetes_namespace_v1" "platform" {
  for_each = local.namespaces
  metadata {
    name = each.key
    labels = {
      "dmf.ebu.ch/layer"                   = each.value.layer
      "pod-security.kubernetes.io/enforce" = each.value.psa
      "pod-security.kubernetes.io/warn"    = "restricted"
      "pod-security.kubernetes.io/audit"   = "restricted"
    }
  }
}

# Security vertical: local PKI and cluster-wide guardrails.
resource "helm_release" "cert_manager" {
  name       = "cert-manager"
  namespace  = kubernetes_namespace_v1.platform["cert-manager"].metadata[0].name
  repository = "https://charts.jetstack.io"
  chart      = "cert-manager"
  version    = var.chart_versions.cert_manager
  wait       = true
  values = [yamlencode({
    crds       = { enabled = true }
    prometheus = { servicemonitor = { enabled = false } }
  })]
}

# CA issuers, Traefik TLS 1.3 policy, MXL Flow Domain admission policy,
# DMF alert rules and dashboards. Needs the CRDs from cert-manager, Traefik
# (shipped with k3s) and the Prometheus Operator.
resource "helm_release" "platform_config" {
  name      = "dmf-platform-config"
  namespace = kubernetes_namespace_v1.platform["dmf-system"].metadata[0].name
  chart     = "${var.charts_dir}/dmf-platform-config"
  wait      = true
  values = [yamlencode({
    # Local chart: hash its files so edits trigger an upgrade (see workload stack).
    chartHash = sha1(join("", [for f in sort(fileset("${var.charts_dir}/dmf-platform-config", "**")) :
    filesha1("${var.charts_dir}/dmf-platform-config/${f}")]))
    certManagerNamespace = helm_release.cert_manager.namespace
    monitoringNamespace  = helm_release.monitoring.namespace
  })]
}

data "kubernetes_secret_v1" "root_ca" {
  metadata {
    name      = "dmf-root-ca"
    namespace = helm_release.cert_manager.namespace
  }
  depends_on = [helm_release.platform_config]
}

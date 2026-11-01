# Monitoring vertical (DMF RA: Prometheus, Grafana for host, container and
# media-function telemetry; alerts; energy where available).
resource "random_password" "grafana_admin" {
  length  = 24
  special = false
}

resource "random_password" "grafana_oidc" {
  length  = 40
  special = false
}

locals {
  grafana_host = "grafana.${var.base_domain}"
}

resource "kubernetes_secret_v1" "grafana_oidc" {
  metadata {
    name      = "grafana-oidc"
    namespace = kubernetes_namespace_v1.platform["dmf-monitoring"].metadata[0].name
  }
  data = { client_secret = random_password.grafana_oidc.result }
}

resource "helm_release" "monitoring" {
  name       = "dmf-monitoring"
  namespace  = kubernetes_namespace_v1.platform["dmf-monitoring"].metadata[0].name
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = var.chart_versions.kube_prometheus_stack
  timeout    = 900
  values = [yamlencode({
    # k3s runs these inside the k3s process; there are no separate endpoints to scrape.
    kubeControllerManager = { enabled = false }
    kubeScheduler         = { enabled = false }
    kubeProxy             = { enabled = false }
    kubeEtcd              = { enabled = false }
    prometheus = {
      prometheusSpec = {
        # Pick up ServiceMonitors / rules from every workload namespace.
        serviceMonitorSelectorNilUsesHelmValues = false
        podMonitorSelectorNilUsesHelmValues     = false
        ruleSelectorNilUsesHelmValues           = false
        retention                               = "7d"
        resources                               = { requests = { cpu = "200m", memory = "512Mi" } }
      }
    }
    prometheus-node-exporter = {
      # timex collector = clock sync state and offset (PTP/NTP) for DMF timing alerts.
      extraArgs = ["--collector.timex", "--collector.systemd"]
    }
    grafana = {
      adminPassword = random_password.grafana_admin.result
      ingress = {
        enabled          = true
        ingressClassName = "traefik"
        hosts            = [local.grafana_host]
        annotations      = { "cert-manager.io/cluster-issuer" = "dmf-ca" }
        tls              = [{ secretName = "grafana-tls", hosts = [local.grafana_host] }]
      }
      envValueFrom = {
        GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET = { secretKeyRef = { name = "grafana-oidc", key = "client_secret" } }
      }
      "grafana.ini" = {
        server = { root_url = "https://${local.grafana_host}" }
        auth   = { disable_login_form = false }
        "auth.generic_oauth" = {
          enabled               = true
          name                  = "DMF SSO"
          client_id             = "grafana"
          scopes                = "openid profile email"
          use_pkce              = true
          auth_url              = "${local.keycloak_public}/protocol/openid-connect/auth"
          token_url             = "${local.keycloak_internal}/protocol/openid-connect/token"
          api_url               = "${local.keycloak_internal}/protocol/openid-connect/userinfo"
          groups_attribute_path = "groups"
          role_attribute_path   = "contains(groups[*], '/dmf-admins') && 'Admin' || 'Viewer'"
          allow_sign_up         = true
        }
      }
      sidecar = { dashboards = { enabled = true, searchNamespace = "ALL" } }
    }
  })]
  depends_on = [kubernetes_secret_v1.grafana_oidc]
}

resource "helm_release" "loki" {
  count      = var.enable_logging ? 1 : 0
  name       = "loki"
  namespace  = helm_release.monitoring.namespace
  repository = "https://grafana.github.io/helm-charts"
  chart      = "loki"
  version    = var.chart_versions.loki
  values = [yamlencode({
    deploymentMode = "SingleBinary"
    loki = {
      auth_enabled = false
      commonConfig = { replication_factor = 1 }
      storage      = { type = "filesystem" }
      schemaConfig = { configs = [{
        from  = "2024-01-01", store = "tsdb", object_store = "filesystem", schema = "v13",
        index = { prefix = "index_", period = "24h" }
      }] }
      limits_config = { retention_period = "168h" }
    }
    singleBinary = { replicas = 1 }
    read         = { replicas = 0 }
    write        = { replicas = 0 }
    backend      = { replicas = 0 }
    chunksCache  = { enabled = false }
    resultsCache = { enabled = false }
    lokiCanary   = { enabled = false }
    test         = { enabled = false }
    gateway      = { enabled = false }
  })]
}

resource "helm_release" "alloy" {
  count      = var.enable_logging ? 1 : 0
  name       = "alloy"
  namespace  = helm_release.monitoring.namespace
  repository = "https://grafana.github.io/helm-charts"
  chart      = "alloy"
  version    = var.chart_versions.alloy
  values = [yamlencode({
    alloy = {
      mounts    = { varlog = true }
      configMap = { content = file("${path.module}/files/alloy.river") }
    }
  })]
  depends_on = [helm_release.loki]
}

resource "kubernetes_config_map_v1" "loki_datasource" {
  count = var.enable_logging ? 1 : 0
  metadata {
    name      = "dmf-loki-datasource"
    namespace = helm_release.monitoring.namespace
    labels    = { grafana_datasource = "1" }
  }
  data = {
    "loki.yaml" = yamlencode({
      apiVersion  = 1
      datasources = [{ name = "Loki", type = "loki", access = "proxy", url = "http://loki.${helm_release.monitoring.namespace}.svc:3100" }]
    })
  }
}

resource "helm_release" "kepler" {
  count      = var.enable_energy_monitoring ? 1 : 0
  name       = "kepler"
  namespace  = helm_release.monitoring.namespace
  repository = "https://sustainable-computing-io.github.io/kepler-helm-chart"
  chart      = "kepler"
  version    = var.chart_versions.kepler
  values = [yamlencode({
    serviceMonitor = { enabled = true, labels = { release = "dmf-monitoring" } }
  })]
}

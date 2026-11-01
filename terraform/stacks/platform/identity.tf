# Application & UI security: Keycloak as the facility identity provider
# (OIDC / OAuth 2.0, SSO; DMF RA references EBU R 143). Workload-specific
# clients and role groups are created by the workload stack at PROVISION and
# removed at FINALISE.
locals {
  keycloak_host     = "keycloak.${var.base_domain}"
  keycloak_public   = "https://${local.keycloak_host}/realms/dmf"
  keycloak_internal = "http://keycloak.dmf-identity.svc.cluster.local:8080/realms/dmf"
  identity_ns       = kubernetes_namespace_v1.platform["dmf-identity"].metadata[0].name
}

resource "random_password" "keycloak_admin" {
  length  = 24
  special = false
}

resource "random_password" "operator" {
  for_each = toset(var.operators)
  length   = 16
  special  = false
}

resource "kubernetes_secret_v1" "keycloak" {
  metadata {
    name      = "keycloak-bootstrap"
    namespace = local.identity_ns
  }
  data = {
    admin-password = random_password.keycloak_admin.result
    "dmf-realm.json" = templatefile("${path.module}/files/realm.json.tftpl", {
      operators      = { for u in var.operators : u => random_password.operator[u].result }
      grafana_secret = random_password.grafana_oidc.result
      grafana_host   = local.grafana_host
      admin_password = random_password.keycloak_admin.result
    })
  }
}

resource "kubernetes_persistent_volume_claim_v1" "keycloak" {
  metadata {
    name      = "keycloak-data"
    namespace = local.identity_ns
  }
  spec {
    access_modes = ["ReadWriteOnce"]
    resources { requests = { storage = "1Gi" } }
  }
  wait_until_bound = false
}

resource "kubernetes_deployment_v1" "keycloak" {
  metadata {
    name      = "keycloak"
    namespace = local.identity_ns
    labels    = { "app.kubernetes.io/name" = "keycloak" }
  }
  spec {
    replicas = 1
    strategy { type = "Recreate" }
    selector { match_labels = { "app.kubernetes.io/name" = "keycloak" } }
    template {
      metadata { labels = { "app.kubernetes.io/name" = "keycloak" } }
      spec {
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = 1000
          fs_group        = 1000
          seccomp_profile { type = "RuntimeDefault" }
        }
        container {
          name  = "keycloak"
          image = var.images.keycloak
          args  = ["start", "--import-realm"]
          env {
            name  = "KC_DB"
            value = "dev-file" # single-node local facility; use PostgreSQL for production
          }
          env {
            name  = "KC_HOSTNAME"
            value = "https://${local.keycloak_host}"
          }
          env {
            name  = "KC_HOSTNAME_BACKCHANNEL_DYNAMIC"
            value = "true"
          }
          env {
            name  = "KC_HTTP_ENABLED"
            value = "true"
          }
          env {
            name  = "KC_PROXY_HEADERS"
            value = "xforwarded"
          }
          env {
            name  = "KC_HEALTH_ENABLED"
            value = "true"
          }
          env {
            name  = "KC_METRICS_ENABLED"
            value = "true"
          }
          env {
            name  = "KC_BOOTSTRAP_ADMIN_USERNAME"
            value = "admin"
          }
          env {
            name = "KC_BOOTSTRAP_ADMIN_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.keycloak.metadata[0].name
                key  = "admin-password"
              }
            }
          }
          port {
            name           = "http"
            container_port = 8080
          }
          port {
            name           = "management"
            container_port = 9000
          }
          readiness_probe {
            http_get {
              path = "/health/ready"
              port = "management"
            }
            period_seconds = 10
          }
          startup_probe {
            http_get {
              path = "/health/started"
              port = "management"
            }
            period_seconds    = 5
            failure_threshold = 60
          }
          resources {
            requests = { cpu = "250m", memory = "768Mi" }
            limits   = { memory = "1536Mi" }
          }
          security_context {
            # Set explicitly: the provider otherwise sends runAsNonRoot=false
            # here, which overrides the pod-level setting and fails PSA.
            run_as_non_root            = true
            allow_privilege_escalation = false
            capabilities { drop = ["ALL"] }
          }
          volume_mount {
            name       = "data"
            mount_path = "/opt/keycloak/data"
          }
          volume_mount {
            name       = "realm"
            mount_path = "/opt/keycloak/data/import"
            read_only  = true
          }
        }
        volume {
          name = "data"
          persistent_volume_claim { claim_name = kubernetes_persistent_volume_claim_v1.keycloak.metadata[0].name }
        }
        volume {
          name = "realm"
          secret {
            secret_name = kubernetes_secret_v1.keycloak.metadata[0].name
            items {
              key  = "dmf-realm.json"
              path = "dmf-realm.json"
            }
          }
        }
      }
    }
  }
  timeouts { create = "10m" }
}

resource "kubernetes_service_v1" "keycloak" {
  metadata {
    name      = "keycloak"
    namespace = local.identity_ns
  }
  spec {
    selector = { "app.kubernetes.io/name" = "keycloak" }
    port {
      name        = "http"
      port        = 8080
      target_port = "http"
    }
  }
}

resource "kubernetes_ingress_v1" "keycloak" {
  metadata {
    name      = "keycloak"
    namespace = local.identity_ns
    annotations = {
      "cert-manager.io/cluster-issuer" = "dmf-ca"
    }
  }
  spec {
    ingress_class_name = "traefik"
    tls {
      hosts       = [local.keycloak_host]
      secret_name = "keycloak-tls"
    }
    rule {
      host = local.keycloak_host
      http {
        path {
          path      = "/"
          path_type = "Prefix"
          backend {
            service {
              name = kubernetes_service_v1.keycloak.metadata[0].name
              port { name = "http" }
            }
          }
        }
      }
    }
  }
  depends_on = [helm_release.platform_config]
}

# Only the ingress controller, SSO gateways and Grafana may reach Keycloak.
resource "kubernetes_network_policy_v1" "keycloak" {
  metadata {
    name      = "keycloak"
    namespace = local.identity_ns
  }
  spec {
    pod_selector { match_labels = { "app.kubernetes.io/name" = "keycloak" } }
    policy_types = ["Ingress"]
    ingress {
      from {
        namespace_selector { match_labels = { "kubernetes.io/metadata.name" = "kube-system" } }
      }
      from {
        namespace_selector { match_labels = { "kubernetes.io/metadata.name" = "dmf-monitoring" } }
      }
      from {
        namespace_selector {
          match_expressions {
            key      = "dmf.ebu.ch/workload"
            operator = "Exists"
          }
        }
        pod_selector { match_labels = { "app.kubernetes.io/component" = "sso-gateway" } }
      }
      ports {
        port     = "8080"
        protocol = "TCP"
      }
    }
  }
}

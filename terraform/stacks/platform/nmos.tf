# Media Exchange discovery: AMWA IS-04 registry (nmos-cpp). The mxl-flow-agent
# of every workload registers its MXL flows here. Cluster-internal only;
# browse it with `make nmos-query` (port-forward).
locals {
  system_ns = kubernetes_namespace_v1.platform["dmf-system"].metadata[0].name
}

resource "kubernetes_config_map_v1" "nmos_registry" {
  metadata {
    name      = "nmos-registry"
    namespace = local.system_ns
  }
  data = {
    "registry.json" = jsonencode({
      pri                          = 10
      label                        = "dmf-registry"
      logging_level                = 0
      http_port                    = 8010
      query_ws_port                = 8011
      registration_expiry_interval = 12
      host_name                    = "nmos-registry.${local.system_ns}.svc.cluster.local"
    })
  }
}

resource "kubernetes_deployment_v1" "nmos_registry" {
  metadata {
    name      = "nmos-registry"
    namespace = local.system_ns
    labels    = { "app.kubernetes.io/name" = "nmos-registry" }
  }
  spec {
    replicas = 1
    selector { match_labels = { "app.kubernetes.io/name" = "nmos-registry" } }
    template {
      metadata { labels = { "app.kubernetes.io/name" = "nmos-registry" } }
      spec {
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = 65532
          run_as_group    = 65532
          seccomp_profile { type = "RuntimeDefault" }
        }
        container {
          name  = "registry"
          image = var.images.nmos_registry
          # Passing args makes the image's entrypoint exec them directly,
          # skipping its avahi / MQTT / switch-PTP bootstrap.
          args = ["/home/nmos-cpp-registry", "/etc/nmos/registry.json"]
          port {
            name           = "http"
            container_port = 8010
          }
          port {
            name           = "query-ws"
            container_port = 8011
          }
          readiness_probe {
            http_get {
              path = "/x-nmos/registration/v1.3/"
              port = "http"
            }
          }
          resources {
            requests = { cpu = "50m", memory = "64Mi" }
            limits   = { memory = "256Mi" }
          }
          security_context {
            # Set explicitly: the provider otherwise sends runAsNonRoot=false
            # here, which overrides the pod-level setting and fails PSA.
            run_as_non_root            = true
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities { drop = ["ALL"] }
          }
          volume_mount {
            name       = "config"
            mount_path = "/etc/nmos"
            read_only  = true
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }
        volume {
          name = "config"
          config_map { name = kubernetes_config_map_v1.nmos_registry.metadata[0].name }
        }
        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "nmos_registry" {
  metadata {
    name      = "nmos-registry"
    namespace = local.system_ns
  }
  spec {
    selector = { "app.kubernetes.io/name" = "nmos-registry" }
    port {
      name        = "http"
      port        = 8010
      target_port = "http"
    }
    port {
      name        = "query-ws"
      port        = 8011
      target_port = "query-ws"
    }
  }
}

resource "kubernetes_network_policy_v1" "nmos_registry" {
  metadata {
    name      = "nmos-registry"
    namespace = local.system_ns
  }
  spec {
    pod_selector { match_labels = { "app.kubernetes.io/name" = "nmos-registry" } }
    policy_types = ["Ingress"]
    ingress {
      from {
        namespace_selector {
          match_expressions {
            key      = "dmf.ebu.ch/workload"
            operator = "Exists"
          }
        }
        pod_selector { match_labels = { "dmf.ebu.ch/role" = "agent" } }
      }
      ports {
        port     = "8010"
        protocol = "TCP"
      }
    }
  }
}

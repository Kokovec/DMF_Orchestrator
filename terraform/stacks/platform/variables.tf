variable "kubeconfig" {
  description = "Kubeconfig written by the Ansible k3s role."
  type        = string
  default     = "../../../.state/kubeconfig"
}

variable "base_domain" {
  description = "DNS suffix for platform and workload UIs, resolved via /etc/hosts. *.localhost cannot be used: browsers resolve it to [::1] first and WebTransport does not fall back to 127.0.0.1."
  type        = string
  default     = "dmf.test"
}

variable "charts_dir" {
  type    = string
  default = "../../../charts"
}

variable "operators" {
  description = "Users created in the DMF realm. Workloads grant them roles via groups /<workload>/<role>."
  type        = list(string)
  default     = ["director1", "audio1", "graphics1", "viewer1"]
}

variable "enable_logging" {
  description = "Loki + Grafana Alloy for container logs (DMF RA container-platform monitoring: logs)."
  type        = bool
  default     = true
}

variable "enable_energy_monitoring" {
  description = "Kepler energy estimation (DMF RA infrastructure monitoring: energy). Needs RAPL/hwmon or falls back to models; off by default because WSL2/VMs expose no power counters."
  type        = bool
  default     = false
}

variable "chart_versions" {
  type = map(string)
  default = {
    cert_manager          = "v1.21.3"
    kube_prometheus_stack = "92.17.0"
    loki                  = "7.5.0"
    alloy                 = "1.15.0"
    kepler                = "0.6.2"
  }
}

variable "images" {
  type = map(string)
  default = {
    keycloak      = "quay.io/keycloak/keycloak:26.8.1"
    nmos_registry = "rhastie/nmos-cpp:master-b6c8367"
  }
}

terraform {
  required_version = ">= 1.9"
  required_providers {
    helm       = { source = "hashicorp/helm", version = "~> 3.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 3.0" }
    random     = { source = "hashicorp/random", version = "~> 3.6" }
    keycloak   = { source = "keycloak/keycloak", version = "~> 5.0" }
  }
}

# One Terraform workspace per Media Workload (selected by Ansible / make).
data "terraform_remote_state" "platform" {
  backend = "local"
  config  = { path = "${path.module}/../platform/terraform.tfstate" }
}

locals {
  platform = data.terraform_remote_state.platform.outputs
}

provider "kubernetes" {
  config_path = var.kubeconfig
}

provider "helm" {
  kubernetes = {
    config_path = var.kubeconfig
  }
}

provider "keycloak" {
  client_id           = "admin-cli"
  username            = "admin"
  password            = local.platform.keycloak_admin_password
  url                 = local.platform.keycloak.admin_url
  realm               = "master"
  root_ca_certificate = local.platform.root_ca_pem
}

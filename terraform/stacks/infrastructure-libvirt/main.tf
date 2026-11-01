# Infrastructure layer, "libvirt" variant: KVM virtual machines on a local lab
# host (Ubuntu 24.04 cloud image + cloud-init). DMF RA: "Deploy operating
# systems ... Tools such as Terraform are typically used by EBU members."
terraform {
  required_version = ">= 1.9"
  required_providers {
    libvirt = { source = "dmacvicar/libvirt", version = "~> 0.8.3" }
  }
}

provider "libvirt" {
  uri = var.libvirt_uri
}

variable "libvirt_uri" {
  type    = string
  default = "qemu:///system"
}

variable "nodes" {
  description = "VMs to create. The first 'server' runs the k3s control plane."
  type = map(object({
    role      = string
    vcpu      = number
    memory_mb = number
    disk_gb   = number
  }))
  default = {
    dmf-node-1 = { role = "server", vcpu = 8, memory_mb = 16384, disk_gb = 60 }
  }
}

variable "ssh_public_key_file" {
  type    = string
  default = "~/.ssh/id_ed25519.pub"
}

variable "base_domain" {
  type    = string
  default = "dmf.test" # RFC 6761 reserved; see platform/variables.tf
}

module "hosts" {
  source         = "../../modules/host-libvirt"
  nodes          = var.nodes
  ssh_public_key = trimspace(file(pathexpand(var.ssh_public_key_file)))
}

module "inventory" {
  source = "../../modules/ansible-inventory"
  path   = "${path.module}/../../../.state/inventory.yml"
  hosts = { for name, n in var.nodes : name => {
    address    = module.hosts.addresses[name]
    connection = "ssh"
    user       = "dmf"
    role       = n.role
  } }
  vars = {
    dmf_base_domain     = var.base_domain
    dmf_infrastructure  = "libvirt"
    dmf_ingress_address = module.hosts.addresses[[for k, n in var.nodes : k if n.role == "server"][0]]
  }
}

output "addresses" {
  value = module.hosts.addresses
}

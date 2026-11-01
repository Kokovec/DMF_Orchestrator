# Infrastructure layer, "localhost" variant: the facility is the machine you
# run this on (e.g. WSL2 Ubuntu 24.04), or a list of existing Linux hosts
# reachable over SSH. Nothing is created; Terraform records the inventory.
terraform {
  required_version = ">= 1.9"
}

variable "hosts" {
  type = map(object({
    address       = string
    connection    = string
    user          = optional(string, "")
    role          = string
    ptp_interface = optional(string, "")
  }))
  default = {
    dmf-node-1 = { address = "127.0.0.1", connection = "local", role = "server" }
  }
}

variable "base_domain" {
  type    = string
  default = "dmf.test" # RFC 6761 reserved; see platform/variables.tf
}

module "inventory" {
  source = "../../modules/ansible-inventory"
  path   = "${path.module}/../../../.state/inventory.yml"
  hosts  = var.hosts
  vars = {
    dmf_base_domain     = var.base_domain
    dmf_infrastructure  = "localhost"
    dmf_ingress_address = values(var.hosts)[0].address
  }
}

output "inventory" {
  value = module.inventory.path
}

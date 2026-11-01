terraform {
  required_providers {
    libvirt = { source = "dmacvicar/libvirt", version = "~> 0.8.3" }
  }
}

variable "nodes" {
  type = map(object({
    role      = string
    vcpu      = number
    memory_mb = number
    disk_gb   = number
  }))
}

variable "ssh_public_key" {
  type = string
}

variable "base_image_url" {
  type    = string
  default = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}

variable "pool_path" {
  type    = string
  default = "/var/lib/libvirt/dmf"
}

variable "network_cidr" {
  type    = string
  default = "10.77.0.0/24"
}

resource "libvirt_pool" "dmf" {
  name = "dmf"
  type = "dir"
  target {
    path = var.pool_path
  }
}

resource "libvirt_network" "dmf" {
  name      = "dmf"
  mode      = "nat"
  domain    = "dmf.lan"
  addresses = [var.network_cidr]
  autostart = true
  dhcp { enabled = true }
  dns { enabled = true }
}

resource "libvirt_volume" "base" {
  name   = "ubuntu-24.04-base.qcow2"
  pool   = libvirt_pool.dmf.name
  source = var.base_image_url
  format = "qcow2"
}

resource "libvirt_volume" "root" {
  for_each       = var.nodes
  name           = "${each.key}-root.qcow2"
  pool           = libvirt_pool.dmf.name
  base_volume_id = libvirt_volume.base.id
  size           = each.value.disk_gb * 1024 * 1024 * 1024
  format         = "qcow2"
}

resource "libvirt_cloudinit_disk" "init" {
  for_each = var.nodes
  name     = "${each.key}-cloudinit.iso"
  pool     = libvirt_pool.dmf.name
  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    hostname       = each.key
    ssh_public_key = var.ssh_public_key
  })
}

resource "libvirt_domain" "host" {
  for_each   = var.nodes
  name       = each.key
  vcpu       = each.value.vcpu
  memory     = each.value.memory_mb
  autostart  = true
  qemu_agent = true
  cloudinit  = libvirt_cloudinit_disk.init[each.key].id

  # Media workloads want the real CPU feature set (AVX for SwitchFrame's SIMD kernels).
  cpu {
    mode = "host-passthrough"
  }

  network_interface {
    network_id     = libvirt_network.dmf.id
    hostname       = each.key
    wait_for_lease = true
  }

  disk {
    volume_id = libvirt_volume.root[each.key].id
  }

  console {
    type        = "pty"
    target_type = "serial"
    target_port = "0"
  }
}

output "addresses" {
  value = { for k, d in libvirt_domain.host : k => d.network_interface[0].addresses[0] }
}

variable "workload_file" {
  description = "Path to the workload definition (DESIGN artefact), e.g. ../../../workloads/news-studio.yaml"
  type        = string
}

variable "kubeconfig" {
  type    = string
  default = "../../../.state/kubeconfig"
}

variable "charts_dir" {
  type    = string
  default = "../../../charts"
}

variable "image_tag" {
  description = "Tag of the locally built dmf.local/* images (make images)."
  type        = string
  default     = "0.1.0"
}

variable "image_ids_file" {
  description = "JSON map of image name to image id, written by make images. A changed id rolls that image's pods; tags stay fixed."
  type        = string
  default     = ""
}

variable "image_registry" {
  type    = string
  default = "dmf.local"
}

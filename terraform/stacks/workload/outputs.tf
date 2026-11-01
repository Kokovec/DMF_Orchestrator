output "workload" {
  value = local.name
}

output "namespace" {
  value = local.ns
}

output "entry_url" {
  description = "Operators start here (Keycloak SSO, then SwitchFrame UI)."
  value       = "https://${local.host}/sso/login"
}

output "hostname" {
  value = local.host
}

output "mxl_domain_path" {
  value = local.domain_path
}

output "flows" {
  description = "MXL flows of this workload (also registered in the NMOS IS-04 registry)."
  value = merge(
    { for fn in keys(local.sources) : "${fn}/video" => local.flow_id["${fn}/video"] },
    { for fn in keys(local.sources) : "${fn}/audio" => local.flow_id["${fn}/audio"] },
    try(local.mixer.programOutput, true) ? {
      "programme/video" = local.flow_id["${local.mixer_key}/video"]
      "programme/audio" = local.flow_id["${local.mixer_key}/audio"]
    } : {},
  )
}

output "switchframe_api_token" {
  description = "Break-glass session token (normal access is via SSO)."
  value       = random_password.api_token.result
  sensitive   = true
}

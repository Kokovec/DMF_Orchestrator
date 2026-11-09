locals {
  wl     = yamldecode(file(var.workload_file))
  name   = local.wl.metadata.name
  spec   = local.wl.spec
  mx     = local.spec.mediaExchange
  access = local.spec.access
  ns     = "wl-${local.name}"

  domain_path = "${local.platform.mxl_root}/${local.mx.domain}"
  host        = local.access.hostname
  roles       = ["director", "audio", "graphics", "captioner", "viewer"]

  # Video standards understood by both SwitchFrame (--format) and the flow definitions
  # (docs/orchestration/02-switchframe-options.md, engine).
  rates = {
    "23.976" = { num = 24000, den = 1001 }
    "24"     = { num = 24, den = 1 }
    "25"     = { num = 25, den = 1 }
    "29.97"  = { num = 30000, den = 1001 }
    "30"     = { num = 30, den = 1 }
    "50"     = { num = 50, den = 1 }
    "59.94"  = { num = 60000, den = 1001 }
    "60"     = { num = 60, den = 1 }
  }
  sizes = {
    "720"  = { width = 1280, height = 720, rates = ["25", "29.97", "30", "50", "59.94", "60"] }
    "1080" = { width = 1920, height = 1080, rates = keys(local.rates) }
    "2160" = { width = 3840, height = 2160, rates = ["25", "29.97", "30", "50", "59.94", "60"] }
  }
  formats = merge([for h, sz in local.sizes : {
    for r in sz.rates : "${h}p${r}" => merge(local.rates[r], { width = sz.width, height = sz.height })
  }]...)

  functions = { for f in local.spec.functions : f.name => f }
  # Input Media Functions: mxl-testsrc (GStreamer test pattern + tone) and
  # mxl-syncsrc (mxl-syncgen: white frame + 1 kHz beep every 30 frames, both
  # derived from the MXL index, so they are aligned by construction).
  input_kinds = ["mxl-testsrc", "mxl-syncsrc"]
  sources     = { for k, f in local.functions : k => f if contains(local.input_kinds, f.kind) }
  mixers      = { for k, f in local.functions : k => f if f.kind == "switchframe" }
  # Null-safe so the namespace preconditions can report a bad design clearly.
  mixer_key = length(local.mixers) == 1 ? keys(local.mixers)[0] : "invalid"
  mixer     = try(local.mixers[local.mixer_key], { format = "1080p29.97", sources = [] })
  rate      = try(local.formats[local.mixer.format], local.formats["1080p29.97"])

  # Flow identity (JT-NM / DMF RA: uniquely identified Flows of timestamped Grains).
  # A generated workload carries the IDs DMF Toolkit published (decision D7). Otherwise
  # deterministic UUIDs, so re-provisioning a workload keeps the same flow ids.
  flow_id = { for pair in setproduct(keys(local.functions), ["video", "audio", "data"]) :
    "${pair[0]}/${pair[1]}" => try(local.functions[pair[0]].flows[pair[1]],
  uuidv5("url", "dmf://${local.name}/${pair[0]}/${pair[1]}")) }

  # The reconciler's config (images/media-functions/sf-reconciler). A generated workload
  # (dmf.local/v1alpha2) comes with the compiled one beside the workload file. A hand-written
  # one gets what the chart's postStart hook used to restore: its first source on air, and each
  # source labelled by its function.
  generated = can(local.mixer.configFile)
  hand_config = {
    apiVersion = "dmf.local/v1alpha1"
    kind       = "SwitchFrameConfig"
    workload   = local.name
    function   = local.mixer_key
    revision   = 0
    program = length(local.mixer.sources) > 0 ? {
      input = local.mixer.sources[0]
      ref   = { kind = "mxl", key = "mxl:${local.flow_id["${local.mixer.sources[0]}/video"]}" }
    } : null
    sources = [for s in local.mixer.sources : {
      input = s
      ref   = { kind = "mxl", key = "mxl:${local.flow_id["${s}/video"]}" }
      apply = [{ method = "POST", path = "/api/sources/{key}/label", body = { label = try(local.functions[s].label, s) } }]
    }]
    global       = []
    destinations = []
    recording    = null
  }
  # A tuple index, because a conditional can't return two objects of different types.
  config = [
    local.hand_config,
    local.generated ? jsondecode(file("${dirname(var.workload_file)}/${try(local.mixer.configFile, "")}")) : null,
  ][local.generated ? 1 : 0]

  # Secret values the config refers to by name: .state/secrets/<id>.yaml (make secret-set),
  # into the workload-refs Secret. Only the referenced ones leave the file.
  secret_refs = toset(flatten([for d in try(local.config.destinations, []) : concat(
    values(try(d.secretFields, {})), try([d.secretUrlSuffix], [])
  )]))
  secret_values = var.secrets_file != "" && fileexists(var.secrets_file) ? try(tomap(yamldecode(file(var.secrets_file))), {}) : {}

  video_flow = { for fn, f in local.functions : fn => {
    id             = local.flow_id["${fn}/video"]
    description    = "${local.name}/${fn} video"
    label          = "${try(f.label, fn)} Video"
    tags           = { "urn:x-nmos:tag:grouphint/v1.0" = ["${fn}:video"] }
    format         = "urn:x-nmos:format:video"
    parents        = []
    media_type     = "video/v210"
    grain_rate     = { numerator = local.rate.num, denominator = local.rate.den }
    frame_width    = local.rate.width, frame_height = local.rate.height
    interlace_mode = "progressive"
    colorspace     = "BT709"
    components = [
      { name = "Y", width = local.rate.width, height = local.rate.height, bit_depth = 10 },
      { name = "Cb", width = local.rate.width / 2, height = local.rate.height, bit_depth = 10 },
      { name = "Cr", width = local.rate.width / 2, height = local.rate.height, bit_depth = 10 },
    ]
  } }

  audio_flow = { for fn, f in local.functions : fn => {
    id            = local.flow_id["${fn}/audio"]
    description   = "${local.name}/${fn} audio"
    label         = "${try(f.label, fn)} Audio"
    tags          = { "urn:x-nmos:tag:grouphint/v1.0" = ["${fn}:audio"] }
    format        = "urn:x-nmos:format:audio"
    parents       = []
    media_type    = "audio/float32"
    sample_rate   = { numerator = 48000 }
    channel_count = 2
    bit_depth     = 32
  } }

  # The Helm provider only notices local-chart edits when the chart version
  # changes; feeding a hash of the chart files in as a value makes any edit
  # trigger an upgrade.
  chart_hash = { for c in ["switchframe", "mxl-function"] : c => sha1(join("", [
    for f in sort(fileset("${var.charts_dir}/${c}", "**")) : filesha1("${var.charts_dir}/${c}/${f}")
  ])) }

  image_ids = var.image_ids_file != "" && fileexists(var.image_ids_file) ? jsondecode(file(var.image_ids_file)) : {}
  images = {
    switchframe = "${var.image_registry}/switchframe"
    mxl_tools   = "${var.image_registry}/mxl-tools"
    sso_bridge  = "${var.image_registry}/sso-bridge"
  }

  common_function_values = {
    workload    = local.name
    podSecurity = { uid = local.mx.uid, gid = local.mx.gid }
    mxl         = { domainPath = local.domain_path }
    image       = { repository = local.images.mxl_tools, tag = var.image_tag }
    imageId     = try(local.image_ids["mxl-tools"], "")
  }
}

# ---------------------------------------------------------------------------
# Container Platform: isolated namespace + reservation (PLAN / PROVISION)
# ---------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "workload" {
  metadata {
    name = local.ns
    labels = {
      "dmf.ebu.ch/workload"    = local.name
      "dmf.ebu.ch/flow-domain" = local.mx.domain
      "dmf.ebu.ch/uid"         = tostring(local.mx.uid)
      # hostPath (Flow Domain) is outside every PSA profile except "privileged";
      # the dmf-media-workload-guardrails ValidatingAdmissionPolicy restores
      # restricted-equivalent rules. warn/audit keep PSA feedback visible.
      "pod-security.kubernetes.io/enforce" = "privileged"
      "pod-security.kubernetes.io/warn"    = "restricted"
      "pod-security.kubernetes.io/audit"   = "restricted"
    }
    annotations = {
      "dmf.ebu.ch/description" = try(local.wl.metadata.description, "")
    }
  }

  lifecycle {
    precondition {
      condition     = length(local.mixers) == 1
      error_message = "A workload needs exactly one function of kind 'switchframe'."
    }
    precondition {
      condition     = local.mx.uid == local.mx.gid && local.mx.uid >= 10000
      error_message = "mediaExchange.uid must equal gid and be >= 10000."
    }
    precondition {
      condition     = contains(keys(local.formats), local.mixer.format)
      error_message = "Unsupported format; use one of: ${join(", ", keys(local.formats))}."
    }
    precondition {
      condition     = alltrue([for s in local.mixer.sources : contains(keys(local.sources), s)])
      error_message = "vision-mixer sources must name input functions (mxl-testsrc, mxl-syncsrc) in this workload."
    }
    precondition {
      condition     = length([for k, f in local.sources : k if f.kind == "mxl-syncsrc"]) == 0 || local.mixer.format == "1080p29.97"
      error_message = "mxl-syncsrc generates 1080p29.97 only (30-frame / 48048-sample period); use it in 1080p29.97 workloads."
    }
  }
}

resource "kubernetes_resource_quota_v1" "reservation" {
  metadata {
    name      = "dmf-reservation"
    namespace = kubernetes_namespace_v1.workload.metadata[0].name
  }
  spec {
    hard = {
      "requests.cpu"            = local.spec.resourceProfile.cpu
      "requests.memory"         = local.spec.resourceProfile.memory
      "requests.nvidia.com/gpu" = tostring(try(local.spec.resourceProfile.gpu, 0))
      "services.loadbalancers"  = "1"
    }
  }
}

resource "kubernetes_limit_range_v1" "defaults" {
  metadata {
    name      = "dmf-defaults"
    namespace = kubernetes_namespace_v1.workload.metadata[0].name
  }
  spec {
    limit {
      type            = "Container"
      default_request = { cpu = "50m", memory = "64Mi" }
      default         = { cpu = "500m", memory = "256Mi" }
    }
  }
}

resource "kubernetes_network_policy_v1" "default_deny" {
  metadata {
    name      = "default-deny"
    namespace = kubernetes_namespace_v1.workload.metadata[0].name
  }
  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
  }
}

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
resource "random_password" "api_token" {
  length  = 64
  special = false
}

resource "random_password" "admin_token" {
  length  = 48
  special = false
}

resource "random_password" "invite" {
  for_each = toset(local.roles)
  length   = 32
  special  = false
}

resource "random_password" "cookie_secret" {
  length  = 32 # oauth2-proxy needs 16, 24 or 32 bytes
  special = false
}

resource "kubernetes_secret_v1" "switchframe" {
  metadata {
    name      = "switchframe-tokens"
    namespace = kubernetes_namespace_v1.workload.metadata[0].name
  }
  data = {
    api-token     = random_password.api_token.result
    admin-token   = random_password.admin_token.result
    invite-tokens = join(",", [for r in local.roles : "${r}:${random_password.invite[r].result}"])
  }
}

# Values the reconciler mounts as files (/etc/switchframe/secrets/<name>): an SRT output's
# passphrase, an RTMP stream key. They reach Terraform state, which is local and git-ignored and
# already holds this workload's tokens.
resource "kubernetes_secret_v1" "workload_refs" {
  metadata {
    name      = "workload-refs"
    namespace = kubernetes_namespace_v1.workload.metadata[0].name
  }
  data = sensitive({ for n in local.secret_refs : n => trimspace(tostring(local.secret_values[n])) if can(local.secret_values[n]) })

  lifecycle {
    precondition {
      condition     = alltrue([for n in local.secret_refs : can(local.secret_values[n])])
      error_message = "Secrets ${join(", ", [for n in local.secret_refs : n if !can(local.secret_values[n])])} are missing from ${var.secrets_file}: make secret-set WORKLOAD=${local.name} NAME=<name>."
    }
  }
}

# ---------------------------------------------------------------------------
# Application & UI security: per-workload OIDC client and role groups
# ---------------------------------------------------------------------------
data "keycloak_realm" "dmf" {
  realm = "dmf"
}

resource "keycloak_openid_client" "workload" {
  realm_id                     = data.keycloak_realm.dmf.id
  client_id                    = "wl-${local.name}"
  name                         = "DMF workload ${local.name}"
  access_type                  = "CONFIDENTIAL"
  standard_flow_enabled        = true
  direct_access_grants_enabled = false
  pkce_code_challenge_method   = "S256"
  valid_redirect_uris          = ["https://${local.host}/oauth2/callback"]
  web_origins                  = ["https://${local.host}"]
}

# The client keeps the realm's built-in default scopes (basic = "sub", profile,
# email, ...). Group paths (/<workload>/<role>) go into the tokens through a
# mapper on the client itself; oauth2-proxy and sso-bridge read that claim.
resource "keycloak_openid_group_membership_protocol_mapper" "groups" {
  realm_id   = data.keycloak_realm.dmf.id
  client_id  = keycloak_openid_client.workload.id
  name       = "groups"
  claim_name = "groups"
  full_path  = true
}

resource "keycloak_group" "workload" {
  realm_id = data.keycloak_realm.dmf.id
  name     = local.name
}

resource "keycloak_group" "role" {
  for_each  = toset(local.roles)
  realm_id  = data.keycloak_realm.dmf.id
  parent_id = keycloak_group.workload.id
  name      = each.key
}

locals {
  members = { for role, users in try(local.access.members, {}) : role => users if length(users) > 0 }
}

data "keycloak_user" "member" {
  for_each = toset(flatten(values(local.members)))
  realm_id = data.keycloak_realm.dmf.id
  username = each.key
}

resource "keycloak_group_memberships" "role" {
  for_each   = local.members
  realm_id   = data.keycloak_realm.dmf.id
  group_id   = keycloak_group.role[each.key].id
  members    = each.value
  depends_on = [data.keycloak_user.member]
}

resource "kubernetes_secret_v1" "sso" {
  metadata {
    name      = "sso-gateway"
    namespace = kubernetes_namespace_v1.workload.metadata[0].name
  }
  data = {
    client-secret = keycloak_openid_client.workload.client_secret
    cookie-secret = random_password.cookie_secret.result
  }
}

# ---------------------------------------------------------------------------
# Media Functions (PROVISION) and initial connections (CONFIGURE)
# ---------------------------------------------------------------------------
resource "helm_release" "source" {
  for_each  = local.sources
  name      = each.key
  namespace = kubernetes_namespace_v1.workload.metadata[0].name
  chart     = "${var.charts_dir}/mxl-function"
  wait      = true
  values = [yamlencode(merge(local.common_function_values, {
    chartHash = local.chart_hash["mxl-function"]
    role      = "input"
    command   = each.value.kind == "mxl-syncsrc" ? ["/usr/local/bin/mxl-syncgen"] : ["/usr/local/bin/mxl-gst-testsrc"]
    args = concat(
      ["-d", local.domain_path, "-v", "/etc/mxl/flows/video.json", "-a", "/etc/mxl/flows/audio.json"],
      each.value.kind == "mxl-testsrc" ? ["-p", try(each.value.pattern, "smpte"), "-t", try(each.value.overlayText, each.value.label, each.key), "-g", each.key] : [],
    )
    flowDefs = { "video.json" = local.video_flow[each.key], "audio.json" = local.audio_flow[each.key] }
    resources = try(each.value.resources, {
      requests = { cpu = "250m", memory = "256Mi" }
      limits   = { cpu = "1", memory = "1Gi" }
    })
  }))]
  depends_on = [kubernetes_resource_quota_v1.reservation, kubernetes_limit_range_v1.defaults]
}

resource "helm_release" "flow_agent" {
  name      = "mxl-flow-agent"
  namespace = kubernetes_namespace_v1.workload.metadata[0].name
  chart     = "${var.charts_dir}/mxl-function"
  values = [yamlencode(merge(local.common_function_values, {
    chartHash = local.chart_hash["mxl-function"]
    role      = "agent"
    command   = ["python3", "-I", "/usr/local/bin/mxl-flow-agent"]
    env = {
      NMOS_REGISTRY_URL = local.platform.nmos_registry_url
      METRICS_ADDR      = "0.0.0.0:9102"
    }
    metrics       = { port = 9102 }
    resources     = { requests = { cpu = "50m", memory = "64Mi" }, limits = { cpu = "500m", memory = "256Mi" } }
    networkPolicy = { egress = [{ namespace = "dmf-system", port = 8010 }] }
  }))]
  depends_on = [kubernetes_resource_quota_v1.reservation]
}

resource "helm_release" "mixer" {
  name      = local.mixer_key
  namespace = kubernetes_namespace_v1.workload.metadata[0].name
  chart     = "${var.charts_dir}/switchframe"
  timeout   = 600
  values = [yamlencode(merge({
    chartHash   = local.chart_hash["switchframe"]
    workload    = local.name
    image       = { repository = local.images.switchframe, tag = var.image_tag }
    imageId     = try(local.image_ids["switchframe"], "")
    podSecurity = { uid = local.mx.uid, gid = local.mx.gid }
    # Applied by the pod's reconciler, on every start and as sources appear: SwitchFrame
    # forgets its programme source, source settings, destinations and recording.
    config = local.config
    reconciler = {
      image   = { repository = local.images.mxl_tools, tag = var.image_tag }
      imageId = try(local.image_ids["mxl-tools"], "")
    }
    mxl = {
      domainPath = local.domain_path
      # A generated workload lists its inputs' flows as wired, data flow included.
      sources = try(local.mixer.mxlSources, [for s in local.mixer.sources : {
        video = local.flow_id["${s}/video"]
        audio = local.flow_id["${s}/audio"]
      }])
      output = {
        enabled   = try(local.mixer.programOutput, true)
        name      = "program"
        videoFlow = merge(local.video_flow[local.mixer_key], { label = "Programme Video" })
        audioFlow = merge(local.audio_flow[local.mixer_key], { label = "Programme Audio" })
      }
    }
    switchframe = merge(
      {
        format         = local.mixer.format
        quicPort       = local.access.quicPort
        srtPort        = try(local.access.srtPort, 0)
        extraArgs      = try(local.mixer.extraArgs, [])
        existingSecret = kubernetes_secret_v1.switchframe.metadata[0].name
        gpu            = try(local.spec.resourceProfile.gpu, 0)
      },
      can(local.mixer.resources) ? { resources = local.mixer.resources } : {},
      can(local.mixer.goMemLimit) ? { goMemLimit = local.mixer.goMemLimit } : {},
    )
    ingress = { hostname = local.host }
    sso = {
      issuerUrl      = local.platform.keycloak.public_url
      internalUrl    = local.platform.keycloak.internal_url
      clientId       = keycloak_openid_client.workload.client_id
      existingSecret = kubernetes_secret_v1.sso.metadata[0].name
      secretChecksum = sha256("${keycloak_openid_client.workload.client_secret}/${random_password.cookie_secret.result}")
      bridgeImage    = { repository = local.images.sso_bridge, tag = var.image_tag }
      bridgeImageId  = try(local.image_ids["sso-bridge"], "")
    }
    },
    # Generated workloads only, so a hand-written one's values stay as they were. A for
    # expression, because a conditional can't return this object or {}: their types differ.
    [for g in(local.generated ? [true] : []) : {
      persistence = { size = "${local.mixer.volumes.stateGi}Gi" }
      cache       = { size = "${local.mixer.volumes.cacheGi}Gi" }
      recordings  = { size = can(local.mixer.volumes.recordingsGi) ? "${local.mixer.volumes.recordingsGi}Gi" : "" }
    }]...

  ))]
  # Sources must be writing before SwitchFrame opens their flows.
  depends_on = [helm_release.source, kubernetes_secret_v1.sso, kubernetes_secret_v1.workload_refs, keycloak_group_memberships.role, keycloak_openid_group_membership_protocol_mapper.groups]
}

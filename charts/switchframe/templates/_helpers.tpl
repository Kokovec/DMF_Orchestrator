{{- define "switchframe.fullname" -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{- define "switchframe.labels" -}}
app.kubernetes.io/name: switchframe
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
dmf.ebu.ch/workload: {{ required "workload is required" .Values.workload }}
dmf.ebu.ch/layer: media-function
{{- end -}}

{{- define "switchframe.selector" -}}
app.kubernetes.io/name: switchframe
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: media-function
{{- end -}}

{{- define "switchframe.gatewaySelector" -}}
app.kubernetes.io/name: switchframe
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: sso-gateway
{{- end -}}

{{- define "switchframe.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ .Values.podSecurity.uid }}
runAsGroup: {{ .Values.podSecurity.gid }}
fsGroup: {{ .Values.podSecurity.gid }}
fsGroupChangePolicy: OnRootMismatch
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "switchframe.containerSecurityContext" -}}
allowPrivilegeEscalation: false
privileged: false
readOnlyRootFilesystem: {{ .Values.podSecurity.readOnlyRootFilesystem }}
capabilities:
  drop: ["ALL"]
{{- end -}}

{{- define "switchframe.mxlSources" -}}
{{- $specs := list -}}
{{- range .Values.mxl.sources -}}
{{- $spec := .video -}}
{{- if .audio }}{{ $spec = printf "%s:%s" $spec .audio }}{{ end -}}
{{- if .data }}{{ $spec = printf "%s:%s" $spec .data }}{{ end -}}
{{- $specs = append $specs $spec -}}
{{- end -}}
{{- join "," $specs -}}
{{- end -}}

{{- define "mxlfn.labels" -}}
app.kubernetes.io/name: mxl-function
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
dmf.ebu.ch/workload: {{ required "workload is required" .Values.workload }}
dmf.ebu.ch/layer: media-function
dmf.ebu.ch/role: {{ .Values.role }}
{{- end -}}

{{- define "mxlfn.selector" -}}
app.kubernetes.io/name: mxl-function
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

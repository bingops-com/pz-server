{{- define "pz-server.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "pz-server.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "pz-server.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "pz-server.labels" -}}
app.kubernetes.io/name: {{ include "pz-server.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}

{{- define "pz-server.selectorLabels" -}}
app.kubernetes.io/name: {{ include "pz-server.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "pz-server.resticRepository" -}}
{{- printf "s3:%s/%s/%s" (trimSuffix "/" .Values.backup.endpoint) .Values.backup.bucket (trimPrefix "/" .Values.backup.prefix) -}}
{{- end -}}

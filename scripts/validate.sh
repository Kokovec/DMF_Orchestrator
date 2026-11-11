#!/usr/bin/env bash
# Static validation of the whole package; no cluster needed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$ROOT/.tools/bin:$PATH"
export ANSIBLE_CONFIG="$ROOT/ansible/ansible.cfg"
cd "$ROOT"
fail=0
step() { printf '\n== %s\n' "$*"; }
run() { "$@" || { echo "FAILED: $*"; fail=1; }; }

step "python"
run python3 -m py_compile images/media-functions/mxl-flow-agent/agent.py images/media-functions/sf-reconciler/reconciler.py \
  images/sso-bridge/bridge.py ansible/scripts/capacity.py ansible/scripts/storage.py scripts/secret-set.py $(find tools/dmf_compile -name '*.py')

step "flow agent tests (unreachable NMOS registry)"
run env PYTHONDONTWRITEBYTECODE=1 python3 -I images/media-functions/mxl-flow-agent/test_agent.py

step "reconciler tests (fake SwitchFrame API)"
# python3 -I, as in the pod: the reconciler is standard library only.
run env PYTHONDONTWRITEBYTECODE=1 python3 -I images/media-functions/sf-reconciler/test_reconciler.py

step "workload compiler tests"
# DMF_REQUIRE_TOOLS: tests that shell out to helm, yamllint, ansible-playbook or docker fail
# instead of skipping when the tool is missing (docker is optional: see the test).
run env DMF_REQUIRE_TOOLS=1 PYTHONDONTWRITEBYTECODE=1 .tools/venv/bin/python -m unittest discover -s tools/dmf_compile/tests -t . -v

step "helm lint + render"
SF_VALUES=$(mktemp); trap 'rm -f "$SF_VALUES"' EXIT
cat > "$SF_VALUES" <<'YAML'
workload: news-studio
mxl:
  domainPath: /var/lib/dmf/mxl/news-studio
  sources: [{video: a0000001-0000-0000-0000-000000000001, audio: a0000001-0000-0000-0000-000000000002}]
  output: {videoFlow: {id: x}, audioFlow: {id: y}}
switchframe: {existingSecret: switchframe-tokens}
ingress: {hostname: news-studio.dmf.test}
sso: {issuerUrl: https://keycloak.dmf.test/realms/dmf, internalUrl: http://keycloak:8080/realms/dmf, existingSecret: sso-gateway}
YAML
run helm lint charts/switchframe -f "$SF_VALUES"
run helm template t charts/switchframe -f "$SF_VALUES" >/dev/null
run helm lint charts/mxl-function --set workload=w --set mxl.domainPath=/var/lib/dmf/mxl/w --set metrics.port=9102
run helm template t charts/mxl-function --set workload=w --set mxl.domainPath=/var/lib/dmf/mxl/w --set metrics.port=9102 >/dev/null
run helm lint charts/dmf-platform-config
run helm template t charts/dmf-platform-config >/dev/null

step "terraform fmt + validate"
run terraform fmt -check -recursive terraform
for s in terraform/stacks/*/; do
  echo "-- $s"
  (cd "$s" && terraform init -backend=false -input=false >/dev/null && terraform validate -no-color) || fail=1
done

step "ansible syntax + lint"
INV=$(mktemp --suffix .yml); trap 'rm -f "$SF_VALUES" "$INV"' EXIT
printf 'all:\n  children:\n    dmf_cluster:\n      children:\n        k3s_server: {hosts: {n1: {ansible_connection: local}}}\n        k3s_agent: {hosts: {}}\n' > "$INV"
for p in ansible/playbooks/*.yml; do
  run ansible-playbook -i "$INV" --syntax-check "$p" -e workload=news-studio
done
(cd ansible && run ansible-lint --offline -q playbooks roles) || true

step "yaml"
run yamllint -d '{extends: relaxed, rules: {line-length: disable}}' workloads ansible/playbooks ansible/roles

exit $fail

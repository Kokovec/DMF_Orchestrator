#!/usr/bin/env bash
# Install pinned control-node tools into ./.tools (no sudo, nothing system-wide):
# terraform, kubectl, helm, ansible-core (+ collections), ansible-lint, yamllint.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$ROOT/.tools"
BIN="$TOOLS/bin"
TERRAFORM_VERSION="${TERRAFORM_VERSION:-1.17.1}"
KUBECTL_VERSION="${KUBECTL_VERSION:-v1.36.5}"
HELM_VERSION="${HELM_VERSION:-v4.3.1}"
mkdir -p "$BIN"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

if ! "$BIN/terraform" version 2>/dev/null | grep -q "v$TERRAFORM_VERSION"; then
  echo ">> terraform $TERRAFORM_VERSION"
  curl -fsSLo "$tmp/tf.zip" "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip"
  python3 -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extract("terraform", sys.argv[2])' "$tmp/tf.zip" "$BIN"
  chmod +x "$BIN/terraform"
fi

if ! "$BIN/kubectl" version --client 2>/dev/null | grep -q "$KUBECTL_VERSION"; then
  echo ">> kubectl $KUBECTL_VERSION"
  curl -fsSLo "$BIN/kubectl" "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
  chmod +x "$BIN/kubectl"
fi

if ! "$BIN/helm" version --short 2>/dev/null | grep -q "$HELM_VERSION"; then
  echo ">> helm $HELM_VERSION"
  curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" | tar -xz -C "$tmp"
  mv "$tmp/linux-amd64/helm" "$BIN/helm"
fi

if [ ! -x "$TOOLS/venv/bin/ansible-playbook" ]; then
  echo ">> ansible (python venv)"
  python3 -m venv --without-pip "$TOOLS/venv"
  curl -fsSLo "$tmp/get-pip.py" https://bootstrap.pypa.io/get-pip.py
  "$TOOLS/venv/bin/python" "$tmp/get-pip.py" -q
  "$TOOLS/venv/bin/pip" install -q "ansible-core>=2.17" ansible-lint yamllint PyYAML
fi
for t in ansible ansible-playbook ansible-galaxy ansible-lint yamllint; do
  ln -sf "$TOOLS/venv/bin/$t" "$BIN/$t"
done

export ANSIBLE_CONFIG="$ROOT/ansible/ansible.cfg"
echo ">> ansible collections"
"$BIN/ansible-galaxy" collection install -r "$ROOT/ansible/requirements.yml" -p "$TOOLS/ansible_collections" >/dev/null

echo "Tools ready in $BIN"

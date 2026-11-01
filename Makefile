# SwitchFrame on an EBU DMF-aligned local facility.
#
# Layers (DMF RA v2.0)          Target
#   Infrastructure              make infra            (INFRA=localhost|libvirt)
#   Host + Container Platform   make hosts
#   Media Function images       make images
#   Shared platform services    make platform
#   Media Workload lifecycle    make plan|provision|status|finalise WORKLOAD=<name>
#
# Everything: make up WORKLOAD=news-studio      Teardown: make finalise / make destroy

SHELL        := /bin/bash
ROOT         := $(abspath .)
export PATH  := $(ROOT)/.tools/bin:$(PATH)
export ANSIBLE_CONFIG := $(ROOT)/ansible/ansible.cfg
export KUBECONFIG := $(ROOT)/.state/kubeconfig
INFRA        ?= localhost
WORKLOAD     ?= news-studio
TF_STACKS    := $(ROOT)/terraform/stacks
PLAYBOOK     := cd ansible && ansible-playbook
BECOME       ?= --ask-become-pass
SKIP_PREFLIGHT ?=
PREFLIGHT    := $(if $(SKIP_PREFLIGHT),true,scripts/preflight.sh)

.PHONY: help prereqs infra hosts images platform plan provision status finalise up destroy windows-browser \
        trust-ca secrets nmos-query lint validate clean

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n",$$1,$$2}'

prereqs: ## Install terraform/kubectl/helm/ansible into ./.tools (no sudo)
	@scripts/prereqs.sh

infra: ## INFRASTRUCTURE: hosts + Ansible inventory (INFRA=localhost|libvirt)
	@$(PREFLIGHT)
	@mkdir -p .state
	cd $(TF_STACKS)/infrastructure-$(INFRA) && terraform init -upgrade -input=false && terraform apply -input=false

hosts: ## HOST + CONTAINER PLATFORM: OS baseline, timing, MXL, k3s
	$(PLAYBOOK) playbooks/site.yml $(BECOME)

images: ## Build Media Function images and load them into every node
	$(PLAYBOOK) playbooks/images.yml $(BECOME)

platform: ## Shared services: PKI, monitoring, Keycloak, NMOS registry, guardrails
	@$(PREFLIGHT)
	$(PLAYBOOK) playbooks/platform.yml $(BECOME)

plan: ## PLAN: validate design, capacity check, terraform plan
	$(PLAYBOOK) playbooks/workload-plan.yml -e workload=$(WORKLOAD)

provision: ## PROVISION + CONFIGURE: Flow Domain, Media Functions, connections
	$(PLAYBOOK) playbooks/workload-provision.yml -e workload=$(WORKLOAD) $(BECOME)

status: ## MONITOR: one-shot status across layers
	$(PLAYBOOK) playbooks/workload-status.yml -e workload=$(WORKLOAD)

finalise: ## FINALISE & REVIEW: archive, tear down, release Flow Domain
	$(PLAYBOOK) playbooks/workload-finalise.yml -e workload=$(WORKLOAD) $(BECOME)

up: infra hosts images platform plan provision ## Everything, end to end

destroy: ## Remove platform services (finalise workloads first) and infrastructure
	cd $(TF_STACKS)/platform && terraform destroy -input=false -var kubeconfig=$(KUBECONFIG) -var charts_dir=$(ROOT)/charts
	cd $(TF_STACKS)/infrastructure-$(INFRA) && terraform destroy -input=false
	@echo "k3s itself stays installed; remove with: sudo /usr/local/bin/k3s-uninstall.sh"

trust-ca: ## Print how to trust the local root CA in browsers
	@test -f .state/dmf-root-ca.crt || { echo "run 'make platform' first"; exit 1; }
	@echo "Root CA: $(ROOT)/.state/dmf-root-ca.crt"
	@echo "  Linux (Chrome/Edge): certutil -d sql:$$HOME/.pki/nssdb -A -t C,, -n 'DMF Local Root CA' -i .state/dmf-root-ca.crt"
	@echo "  Windows (for WSL):   certutil.exe -user -addstore Root \"$$(wslpath -w .state/dmf-root-ca.crt 2>/dev/null || echo .state/dmf-root-ca.crt)\""

secrets: ## Show admin and demo-operator credentials
	@scripts/secrets.sh

windows-browser: ## WSL2: open WORKLOAD's UI in Chrome on Windows (saves WSL CPU)
	@scripts/windows-browser.sh $(WORKLOAD)

nmos-query: ## List flows registered in the NMOS IS-04 registry
	@kubectl -n dmf-system port-forward svc/nmos-registry 18010:8010 >/dev/null & pf=$$!; sleep 2; \
	  curl -s http://127.0.0.1:18010/x-nmos/query/v1.3/flows | python3 -m json.tool; kill $$pf

lint: ## Static checks: helm lint, terraform validate, ansible syntax/lint, python
	@scripts/validate.sh

validate: lint

clean: ## Remove local state (does NOT touch running infrastructure)
	rm -rf .state/archive .state/*.tfplan .state/images.tar

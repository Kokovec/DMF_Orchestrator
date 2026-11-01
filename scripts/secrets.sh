#!/usr/bin/env bash
# Print the facility's generated credentials (from Terraform state).
set -euo pipefail
cd "$(dirname "$0")/../terraform/stacks/platform"
terraform output -json | python3 -c '
import json, sys
o = json.load(sys.stdin)
v = lambda k: o[k]["value"]
print("Keycloak admin console : %s" % v("keycloak")["admin_url"])
print("  user admin           : %s" % v("keycloak_admin_password"))
print("  user dmf-admin       : %s   (realm dmf; Grafana Admin via \"Sign in with DMF SSO\")" % v("keycloak_admin_password"))
print()
print("Operators (realm dmf) - sign in at https://<workload hostname>/sso/login")
for user, pw in sorted(v("operator_passwords").items()):
    print("  %-20s : %s" % (user, pw))
print()
g = v("grafana")
print("Grafana %s" % g["url"])
print("  local user admin     : %s" % g["admin_password"])
'

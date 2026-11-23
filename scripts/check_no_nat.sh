#!/usr/bin/env bash
# Fails when a Terraform directory declares Cloud NAT or a Serverless VPC Access connector
# (ADR-024: Direct VPC egress and Private Google Access, no NAT, no connector).
# Usage: scripts/check_no_nat.sh [dir]   (default: terraform). Exit 0 clean, 1 forbidden resource, 2 error.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

dir=${1:-terraform}
if [ ! -d "${dir}" ]; then
  echo "check_no_nat: directory not found: ${dir}" >&2
  exit 2
fi

# Both spellings of the connector type: the provider's real name and the one the ticket uses.
forbidden='google_compute_router_nat|google_vpc_access_connector|google_compute_vpc_access_connector'

# Only real resource declarations match: a commented-out line does not start with "resource".
# grep exits 0 on a match, 1 on none, above 1 on an error, which must not pass silently.
grep_status=0
matches=$(grep -rnE --include='*.tf' "^[[:space:]]*resource[[:space:]]+\"(${forbidden})\"" "${dir}") || grep_status=$?
if [ "${grep_status}" -eq 0 ]; then
  echo "check_no_nat: forbidden resource under ${dir} (ADR-024):" >&2
  echo "${matches}" >&2
  exit 1
elif [ "${grep_status}" -ne 1 ]; then
  echo "check_no_nat: grep failed (exit ${grep_status})" >&2
  exit 2
fi
echo "check_no_nat: ok (${dir})"

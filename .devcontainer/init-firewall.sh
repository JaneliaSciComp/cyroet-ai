#!/usr/bin/env bash
# Default-deny egress firewall. Only allowlisted domains + the local LAN are reachable.
# Runs once at container start via postCreateCommand.

set -euo pipefail
IFS=$'\n\t'

ALLOWED_DOMAINS=(
  api.anthropic.com
  statsig.anthropic.com
  sentry.io
  github.com
  api.github.com
  codeload.github.com
  objects.githubusercontent.com
  raw.githubusercontent.com
  registry.npmjs.org
  # pixi installer + prebuilt binaries
  pixi.sh
  # prefix.dev channels (pixi default for conda packages)
  prefix.dev
  repo.prefix.dev
  fast.prefix.dev
  # PyPI
  pypi.org
  files.pythonhosted.org
  # conda-forge and anaconda channels
  conda.anaconda.org
  repo.anaconda.com
  anaconda.org
)

# Flush any prior state so re-running is idempotent.
iptables -F
iptables -X
iptables -t nat -F
iptables -t nat -X
iptables -t mangle -F
iptables -t mangle -X
ipset destroy allowed-domains 2>/dev/null || true

# Loopback + DNS + established traffic.
iptables -A INPUT  -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT
iptables -A INPUT  -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Allowlist ipset (IPv4 hash:net).
ipset create allowed-domains hash:net

# Allow the container's own LAN (host network) so VS Code tooling keeps working.
HOST_NET="$(ip route show default | awk '/default/ {print $3}' | head -n1)"
if [[ -n "${HOST_NET}" ]]; then
  GW_CIDR="$(ip route | awk -v gw="${HOST_NET}" '$1 != "default" && $0 ~ gw {print $1; exit}')"
  if [[ -n "${GW_CIDR}" ]]; then
    echo "Allowing host LAN ${GW_CIDR}"
    ipset add allowed-domains "${GW_CIDR}" || true
  fi
fi

# Resolve each domain to A records and add to the ipset.
for domain in "${ALLOWED_DOMAINS[@]}"; do
  echo "Resolving ${domain}..."
  mapfile -t ips < <(dig +short A "${domain}" | grep -E '^[0-9.]+$' || true)
  if [[ ${#ips[@]} -eq 0 ]]; then
    echo "WARNING: no A records for ${domain}" >&2
    continue
  fi
  for ip in "${ips[@]}"; do
    ipset add allowed-domains "${ip}" -exist
  done
done

# GitHub publishes its egress ranges; pull them in too so git clone over HTTPS stays robust.
if gh_ranges=$(curl -fsS --max-time 5 https://api.github.com/meta); then
  echo "${gh_ranges}" | jq -r '.web[], .api[], .git[]' 2>/dev/null \
    | grep -E '^[0-9.]+/[0-9]+$' \
    | while read -r cidr; do ipset add allowed-domains "${cidr}" -exist; done || true
fi

# Default policies: drop everything not explicitly allowed.
iptables -P INPUT   DROP
iptables -P FORWARD DROP
iptables -P OUTPUT  DROP

# Re-allow loopback + DNS + established after policy flip (policy doesn't override rules, but be explicit).
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A INPUT  -i lo -j ACCEPT
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT
iptables -A INPUT  -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Permit egress to the allowlist only.
iptables -A OUTPUT -m set --match-set allowed-domains dst -j ACCEPT

echo "Firewall initialized. Verifying..."
if curl -fsS --max-time 5 https://api.anthropic.com/ -o /dev/null; then
  echo "OK: api.anthropic.com reachable"
else
  echo "WARN: api.anthropic.com check returned non-zero (may be fine if endpoint 404s on GET)"
fi
if curl -fsS --max-time 5 https://example.com/ -o /dev/null; then
  echo "FAIL: example.com should be blocked" >&2
  exit 1
else
  echo "OK: example.com correctly blocked"
fi

#!/usr/bin/env bash
# Caerus VM exposure audit — READ ONLY. Changes nothing.
# Run ON the VM:  bash security_audit_vm.sh
# Or from your laptop:  ssh brettolson@alpha-stack-scheduler 'bash -s' < security_audit_vm.sh
set -uo pipefail

line(){ printf '\n=== %s ===\n' "$1"; }

line "HOST / CLOUD"
hostname; echo "uptime:$(uptime -p 2>/dev/null)"
curl -s -m 3 -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null \
  && echo "  <- external IP (has a public address)" || echo "no GCP metadata / no external IP"

line "LISTENING SOCKETS (what is bound, and on which interface)"
# 0.0.0.0 / :: = exposed to all interfaces; 127.0.0.1 / ::1 = local only
if command -v ss >/dev/null; then sudo ss -tulnp 2>/dev/null || ss -tuln; else netstat -tulnp 2>/dev/null; fi

line "PUBLICLY-BOUND PORTS (0.0.0.0 or ::, the ones that matter)"
if command -v ss >/dev/null; then
  sudo ss -tulnp 2>/dev/null | awk 'NR==1 || $5 ~ /0\.0\.0\.0:|\[::\]:|\*:/'
fi

line "HOST FIREWALL (ufw)"
sudo ufw status verbose 2>/dev/null || echo "ufw not installed/active"

line "HOST FIREWALL (nftables / iptables)"
sudo nft list ruleset 2>/dev/null | head -40 || sudo iptables -S 2>/dev/null | head -40 || echo "none readable"

line "GCP VPC FIREWALL RULES (the real perimeter)"
if command -v gcloud >/dev/null; then
  gcloud compute firewall-rules list \
    --format='table(name,network,direction,sourceRanges.list():label=SRC,allowed[].map().firewall_rule().list():label=ALLOW,disabled)' 2>/dev/null \
    || echo "gcloud present but list failed (auth/project?)"
else
  echo "gcloud CLI not on this host — check VPC rules in the Cloud Console:"
  echo "  https://console.cloud.google.com/networking/firewalls/list"
fi

line "NGINX — SITES + AUTH + TLS"
echo "-- enabled sites --"; ls -l /etc/nginx/sites-enabled/ 2>/dev/null
echo "-- listen / ssl / auth_basic directives --"
sudo grep -rEn 'listen|ssl_certificate|auth_basic|allow|deny|proxy_pass' /etc/nginx/sites-enabled/ 2>/dev/null
echo "-- htpasswd files present? --"
sudo ls -l /etc/nginx/.htpasswd* 2>/dev/null || echo "  NO htpasswd file found (basic_auth would 403 everyone, or isn't enforced)"

line "SSH HARDENING"
sudo grep -Ei '^\s*(PermitRootLogin|PasswordAuthentication|PubkeyAuthentication|Port|AllowUsers)' \
  /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null || echo "sshd_config not readable"
command -v fail2ban-client >/dev/null && sudo fail2ban-client status 2>/dev/null || echo "fail2ban: not installed"

line "PRIVATE-NETWORK OPTIONS ALREADY PRESENT?"
command -v tailscale >/dev/null && { echo "tailscale installed:"; sudo tailscale status 2>/dev/null | head; } || echo "tailscale: not installed"
command -v wg >/dev/null && echo "wireguard (wg) installed" || echo "wireguard: not installed"

line "SECRETS / WORLD-READABLE SENSITIVE FILES"
echo "-- alpaca.env perms (should be -rw------- = 600) --"
ls -l "$HOME/.caerus/alpaca.env" 2>/dev/null || echo "  not at ~/.caerus/alpaca.env"
echo "-- anything sensitive sitting INSIDE the web root? (should be none) --"
sudo find /var/www -type f \( -name '*.env' -o -name '*.key' -o -name '*.pem' -o -name '*secret*' -o -name '*credential*' \) 2>/dev/null || true

line "AUDIT COMPLETE — nothing was modified"

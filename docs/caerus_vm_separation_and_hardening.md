# Caerus — VM Separation & Hardening Runbook

**Created:** 2026-06-23 · **Execution target:** 2026-06-24 (tomorrow) · **Owner:** Brett

This is a planning + execution runbook for (1) separating Caerus from the golfbot onto its
own VM and (2) hardening Caerus's new home. Read the rationale once; execute the checklist in
order. **Every step that removes access is gated behind a verification of an alternate path —
you should never be one command away from locking yourself out.**

---

## 1. Why we're doing this

Today `alpha-stack-scheduler` (GCP, external IP `34.10.49.26`) runs **two workloads on one box**:

- **Caerus** — sensitive: pulls the live broker, holds `alpaca.env`, serves account balances and
  governance/evidence data.
- **golfbot** — low-stakes: gunicorn on `127.0.0.1:5001`, proxied at `/golf/`.

Co-locating them is wrong on two axes:

- **Security blast radius.** Shared kernel, network stack, nginx, and public IP. A compromise of
  the workload we care *least* about (golfbot) lands the attacker on the same box as the broker
  credentials and account data. Your effective security is set by your *weakest* tenant.
- **Operational coupling.** One OS to patch, one nginx, shared deps. A reboot or a bad golfbot
  deploy takes Caerus down too. This worsens over time.

## 2. Current exposure (from the 2026-06-23 audit)

- Internet-facing ports: **22 (sshd)** and **80 (nginx)**. golfbot is local-only (good).
- **No host firewall** — ufw inactive, no iptables/nftables. Perimeter rests entirely on the GCP
  VPC firewall, **which is currently unverified** (gcloud not authenticated on the box).
- SSH is **key-only** (`PasswordAuthentication no`) — good. No fail2ban.
- Dashboard basic-auth is real (htpasswd exists) but runs over **plain HTTP** — password and
  account data cross the network in clear text. **No TLS.**
- `alpaca.env` is `600`, nothing sensitive in the web root — good.

## 3. Target architecture

**Two separate VMs.** Strongest isolation: independent kernels, firewalls, perimeters, patching,
reboots, and blast radius. A golfbot compromise cannot reach Caerus.

- **golfbot** — stays on the current VM. Can remain public and easy; we care less.
- **Caerus** — moves to its own new VM, **private by default**:
  - Reachable only over **Tailscale** (no public dashboard/SSH).
  - Dashboard served over **HTTPS automatically** via `tailscale serve` (no cert management).
  - GCP firewall **default-deny ingress**; the only allowed path in is Tailscale + an IAP
    break-glass for SSH.
  - Host `ufw` on as defense-in-depth.

Rejected alternatives: same-VM Unix users (shared nginx/kernel — barely a boundary) and same-VM
containers (clean ops separation but still one shared kernel/host). Neither removes the blast-radius
coupling, which is the whole point.

> **Sequencing principle:** build the new Caerus VM **already hardened**, migrate onto it, verify,
> then pull Caerus's exposure off the old VM. Harden once — on the box that keeps the data — not
> twice.

---

## 4. BLOCKER to resolve before migration detail is final

**What does Caerus actually run on `alpha-stack-scheduler`?**

- If it's **just the dashboard refresh** (broker pull → `dashboard_data.json` every 5 min), the
  footprint is small and the migration is light. The empty cockpit + audit suggest research and
  execution artifacts are generated on the **Mac**, not the VM, which points this way.
- If the **live trading / execution pipeline** also runs there, the move is bigger (signals,
  execution, schedulers, more secrets).

→ Confirm with: `systemctl list-timers` and `systemctl list-units --type=service | grep -i caerus`
on the VM, plus a look at any cron jobs (`crontab -l`). Fill in the answer here before step 6.

```
Caerus-on-VM inventory (fill in 2026-06-24):
- services/timers:
- cron jobs:
- secrets present:
- verdict: [ light dashboard-only ]  /  [ full trading stack ]
```

---

## 5. Hardening runbook for the NEW Caerus VM

Do these on the freshly provisioned VM, **in order**. Don't proceed to a step until the prior
verification passes.

### 5.1 Provision
- [ ] Create a new GCP VM (smallest that fits; same region is fine), Ubuntu LTS.
- [ ] Do **not** assign a static public IP for the dashboard. (Ephemeral is fine; we're closing it.)
- [ ] Confirm you can SSH in once (via GCP Console SSH or `gcloud compute ssh`).

### 5.2 Stand up the private path (additive — removes nothing)
- [ ] Install Tailscale: `curl -fsSL https://tailscale.com/install.sh | sh`
- [ ] `sudo tailscale up` → authenticate to your tailnet.
- [ ] Install Tailscale on your **Mac** too (if not already) and sign into the same tailnet.
- [ ] **VERIFY:** from the Mac, `ssh brettolson@<vm-tailscale-ip>` works (use the `100.x.y.z`
      address or MagicDNS name). ✅ Only continue once this works.

### 5.3 Break-glass for SSH (so closing public 22 is safe)
- [ ] In the GCP firewall, add an **allow** rule: TCP:22 from **`35.235.240.0/20`** (Google IAP range).
- [ ] **VERIFY:** `gcloud compute ssh <vm> --tunnel-through-iap` connects. ✅
- [ ] (Optional second net: enable serial-console access as a last resort.)

### 5.4 Migrate Caerus (detail depends on §4 verdict)
- [ ] Copy repo to the new VM (`git clone` or rsync over Tailscale).
- [ ] Create the Python venv and install deps.
- [ ] Move secrets securely **over Tailscale**, not the public internet:
      `scp ~/.caerus/alpaca.env brettolson@<vm-tailscale-ip>:~/.caerus/` then `chmod 600`.
- [ ] Recreate the systemd refresh timer/service (and trading units if §4 says "full stack").
- [ ] Point the deploy at the new host: `REMOTE_HOST=brettolson@<new-vm> bash scripts/deploy_dashboard_vm.sh`
      (the script already honors the `REMOTE_HOST` env override).
- [ ] Sync cockpit artifacts (already in the deploy script) and trigger one refresh.

### 5.5 Serve the dashboard privately over HTTPS
- [ ] In the Tailscale admin console: enable **MagicDNS** and **HTTPS certificates**.
- [ ] `sudo tailscale serve --bg 80`  → dashboard available at
      `https://<vm>.<tailnet>.ts.net/dashboard/` (encrypted, tailnet-only).
- [ ] **VERIFY:** load that HTTPS URL from the Mac and confirm the dashboard renders. ✅
- [ ] Keep basic-auth as a second layer (defense-in-depth), now over TLS.

### 5.6 Close the public doors (only after 5.2–5.5 all verified)
- [ ] GCP firewall: **remove** any allow rule for TCP:80 from `0.0.0.0/0`.
- [ ] GCP firewall: **remove** public TCP:22 from `0.0.0.0/0` (the IAP rule from 5.3 stays).
- [ ] Recall: GCP **default-denies ingress**, so deleting the allow rules closes the ports.
- [ ] **VERIFY from off-tailnet** (e.g., phone on cellular): the public IP no longer answers on
      80 or 22, but the Tailscale URL still works. ✅

### 5.7 Host firewall (defense-in-depth)
- [ ] `sudo ufw default deny incoming`
- [ ] `sudo ufw default allow outgoing`
- [ ] `sudo ufw allow in on tailscale0`
- [ ] `sudo ufw allow from 35.235.240.0/20 to any port 22 proto tcp`  (IAP break-glass)
- [ ] `sudo ufw enable`  ← only after the two allows above are in, or you'll cut yourself off.
- [ ] **VERIFY:** Tailscale SSH and IAP SSH both still connect. ✅

### 5.8 Decommission Caerus on the OLD VM
- [ ] Remove the `/dashboard/` and `/dashboardDEV/` location blocks from the old nginx and reload.
- [ ] Delete the synced artifacts and `dashboard_data.json` from `/var/www/caerus-dashboard*`.
- [ ] Remove `~/.caerus/alpaca.env` and the Caerus systemd timer/service from the old VM.
- [ ] **VERIFY:** old VM no longer serves any Caerus data; golfbot still works.

---

## 6. Post-migration checklist
- [ ] No Caerus port is reachable from the public internet.
- [ ] Dashboard only over `https://…ts.net`, tailnet-only, behind basic-auth.
- [ ] Broker secrets exist only on the new VM, `600`, outside any web root.
- [ ] Old VM holds golfbot only; no Caerus secrets, artifacts, or routes remain.
- [ ] Two break-glass paths into the new VM confirmed (Tailscale SSH + IAP SSH).
- [ ] (Optional) fail2ban — largely unnecessary once 22 is off the public internet.

## 7. Notes / decisions log
- 2026-06-23: Agreed to separate VMs (not same-VM virtual separation). Target: Caerus private via
  Tailscale, default-deny firewall, HTTPS via `tailscale serve`. golfbot stays on old VM, public.
- Open: §4 scope (dashboard-only vs full trading stack) — resolve first thing 2026-06-24.

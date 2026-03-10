# VM Creation Guidelines for AI Agents

This document describes the standard configuration for VMs that run AI agents.

> **Note:** Always run `sudo apt update` before installing packages.

## Prerequisites

Before proceeding, ensure you have:
- SSH key pair generated (`ssh-keygen -t ed25519` if needed)
- Access to a cloud provider (Hetzner, GCP, AWS, etc.)
- Basic familiarity with Linux command line and SSH

## Base configuration

### Operating system
- **Ubuntu LTS** (latest version, currently 24.04)
- Apply all security patches immediately after creation
- Enable unattended-upgrades for automatic security updates

### Hardware (recommended minimums)
- 4 vCPUs (dedicated preferred)
- 16 GiB RAM
- 80+ GiB SSD

### Location
- Choose a datacenter region that matches your team's compliance and latency requirements
- Non-default locations may require explicit approval from leadership

## SSH configuration

### Port
- Use non-standard port: **2223**
- Helps reduce automated attack noise

### Authentication
- **Key-based only** — password authentication disabled
- For shared VMs: use keys from the team's shared SSH keys repository
- For isolated/specific-purpose VMs: only add required keys

### Users
- Create non-root user (e.g., `agent`, `ops`, etc.)
- Grant passwordless sudo access
- Never use root for regular operations

### Disable root SSH access
After creating the non-root user, remove root's authorized keys to prevent any SSH access to root:
```bash
# Remove root's SSH keys
rm -f /root/.ssh/authorized_keys
# Or empty the file
> /root/.ssh/authorized_keys
```
This ensures root cannot SSH in even with key-based auth (combined with `PermitRootLogin no` in sshd_config).

### SSH hardening (`/etc/ssh/sshd_config.d/hardening.conf`)
```
Port 2223
PermitRootLogin no
PasswordAuthentication no
PermitEmptyPasswords no
PubkeyAuthentication yes
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
PermitUserEnvironment no
```

**Important for Ubuntu 24.04:** Disable systemd socket activation to use custom port:
```bash
systemctl disable ssh.socket
systemctl stop ssh.socket
systemctl restart ssh
```

## Firewall (UFW)

### Basic setup
```bash
sudo apt install ufw -y
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw limit 2223/tcp comment 'SSH with rate limiting'
sudo ufw enable
```

### Additional ports (as needed)
```bash
# Example for web services
sudo ufw allow 80/tcp comment 'HTTP'
sudo ufw allow 443/tcp comment 'HTTPS'
```

## Fail2Ban

### Installation
```bash
sudo apt install fail2ban -y
```

### Configuration (`/etc/fail2ban/jail.local`)
```ini
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 3
banaction = ufw
backend = systemd

[sshd]
enabled = true
port = 2223
filter = sshd
maxretry = 3
bantime = 24h
```

Note: Do not specify `logpath` when using `backend = systemd` — fail2ban reads from journald directly.

### Enable and start
```bash
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
```

## Automatic security updates

### Installation
```bash
sudo apt install unattended-upgrades apt-listchanges -y
sudo dpkg-reconfigure -plow unattended-upgrades
```

### Verify configuration (`/etc/apt/apt.conf.d/50unattended-upgrades`)
Ensure security updates are enabled:
```
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
```

## Additional hardening

### Disable unnecessary services
```bash
# Check running services
systemctl list-units --type=service --state=running

# Disable unneeded services (examples)
sudo systemctl disable --now avahi-daemon
sudo systemctl disable --now cups
```

### Kernel hardening (`/etc/sysctl.d/99-security.conf`)
```ini
# Disable IP forwarding
net.ipv4.ip_forward = 0
net.ipv6.conf.all.forwarding = 0

# Ignore ICMP broadcast requests
net.ipv4.icmp_echo_ignore_broadcasts = 1

# Disable source routing
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0

# Enable TCP SYN cookies
net.ipv4.tcp_syncookies = 1

# Log martian packets
net.ipv4.conf.all.log_martians = 1
```

Apply with: `sudo sysctl --system`

### AppArmor
Ensure AppArmor is enabled (default on Ubuntu):
```bash
sudo aa-status
```

### File descriptor limits

Increase open file limits for AI agents that may handle many connections/files:

```bash
# /etc/security/limits.d/99-agent.conf
cat << 'EOF' | sudo tee /etc/security/limits.d/99-agent.conf
* soft nofile 65535
* hard nofile 65535
* soft nproc 65535
* hard nproc 65535
EOF
```

Also update systemd defaults:

```bash
# /etc/systemd/system.conf.d/limits.conf
sudo mkdir -p /etc/systemd/system.conf.d
cat << 'EOF' | sudo tee /etc/systemd/system.conf.d/limits.conf
[Manager]
DefaultLimitNOFILE=65535
DefaultLimitNPROC=65535
EOF

sudo systemctl daemon-reexec
```

Verify after relogin:
```bash
ulimit -n  # should show 65535
```

## Cloud-init template

For automated VM creation with proper configuration:

```yaml
#cloud-config
users:
  - name: agent
    groups: sudo
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      # IMPORTANT: Replace with actual SSH key before deployment!
      - ssh-ed25519 YOUR_SSH_PUBLIC_KEY_HERE user@example.com

package_update: true
package_upgrade: true

packages:
  - ufw
  - fail2ban
  - unattended-upgrades
  - apt-listchanges

write_files:
  - path: /etc/ssh/sshd_config.d/hardening.conf
    content: |
      Port 2223
      PermitRootLogin no
      PasswordAuthentication no
      PermitEmptyPasswords no
      PubkeyAuthentication yes
      MaxAuthTries 3
      ClientAliveInterval 300
      ClientAliveCountMax 2
      X11Forwarding no
      AllowAgentForwarding no
      AllowTcpForwarding no
      PermitUserEnvironment no

  - path: /etc/fail2ban/jail.local
    content: |
      [DEFAULT]
      bantime = 1h
      findtime = 10m
      maxretry = 3
      banaction = ufw
      backend = systemd

      [sshd]
      enabled = true
      port = 2223
      maxretry = 3
      bantime = 24h

  - path: /etc/sysctl.d/99-security.conf
    content: |
      net.ipv4.ip_forward = 0
      net.ipv6.conf.all.forwarding = 0
      net.ipv4.icmp_echo_ignore_broadcasts = 1
      net.ipv4.conf.all.accept_source_route = 0
      net.ipv4.conf.default.accept_source_route = 0
      net.ipv6.conf.all.accept_source_route = 0
      net.ipv6.conf.default.accept_source_route = 0
      net.ipv4.tcp_syncookies = 1
      net.ipv4.conf.all.log_martians = 1

runcmd:
  # Wait for cloud-init to finish writing user SSH keys (with verification)
  - |
    for i in $(seq 1 30); do
      [ -f /home/agent/.ssh/authorized_keys ] && [ -s /home/agent/.ssh/authorized_keys ] && break
      sleep 1
    done

  # Disable root SSH access
  - rm -f /root/.ssh/authorized_keys

  # Disable SSH socket activation (Ubuntu 24.04+)
  - systemctl list-unit-files ssh.socket >/dev/null 2>&1 && systemctl disable --now ssh.socket || true
  - systemctl reload ssh || systemctl restart ssh
  - systemctl is-active --quiet ssh || systemctl start ssh

  # Configure UFW
  - ufw default deny incoming
  - ufw default allow outgoing
  - ufw limit 2223/tcp comment 'SSH'
  - ufw --force enable

  # Enable fail2ban
  - systemctl enable fail2ban
  - systemctl start fail2ban

  # Enable unattended upgrades
  - dpkg-reconfigure -plow unattended-upgrades

  # Apply kernel hardening
  - sysctl --system
```

---

## Quick setup commands

To harden a fresh Ubuntu 24.04 server, run as root:

```bash
# Step 1: Create user and add SSH key FIRST (required before SSH changes)
useradd -m -s /bin/bash -G sudo agent && \
echo "agent ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/agent && \
mkdir -p /home/agent/.ssh && \
echo "ssh-ed25519 YOUR_SSH_PUBLIC_KEY_HERE user@example.com" > /home/agent/.ssh/authorized_keys && \
chown -R agent:agent /home/agent/.ssh && \
chmod 700 /home/agent/.ssh && \
chmod 600 /home/agent/.ssh/authorized_keys

# Step 2: Install packages and configure security
apt update && apt upgrade -y && \
apt install -y ufw fail2ban unattended-upgrades && \
echo -e "Port 2223\nPermitRootLogin no\nPasswordAuthentication no\nPermitEmptyPasswords no\nX11Forwarding no\nAllowAgentForwarding no\nAllowTcpForwarding no" > /etc/ssh/sshd_config.d/hardening.conf && \
systemctl disable ssh.socket && systemctl stop ssh.socket && systemctl restart ssh && \
ufw default deny incoming && ufw default allow outgoing && ufw limit 2223/tcp && ufw --force enable && \
echo -e "[DEFAULT]\nbanaction = ufw\nbackend = systemd\n\n[sshd]\nenabled=true\nport=2223\nfilter=sshd\nmaxretry=3\nbantime=24h" > /etc/fail2ban/jail.local && \
systemctl enable fail2ban && systemctl restart fail2ban && \
dpkg-reconfigure -plow unattended-upgrades && \
rm -f /root/.ssh/authorized_keys
```

**Important:** Replace `YOUR_SSH_PUBLIC_KEY_HERE` with your actual SSH public key in Step 1 before running, or you will be locked out!

---

## Server audit checklist

Use this checklist to verify a VM is properly secured before marking it "ready":

### SSH security
- [ ] SSH running on port 2223 (not 22)
- [ ] Password authentication disabled
- [ ] Root login disabled (`PermitRootLogin no`)
- [ ] Root's authorized_keys removed/empty
- [ ] Only authorized keys present for non-root user
- [ ] Non-root user created with sudo access

### Firewall
- [ ] UFW enabled and active
- [ ] Default incoming policy: deny
- [ ] Only required ports open
- [ ] SSH rate limiting enabled

### Intrusion prevention
- [ ] Fail2ban installed and running
- [ ] SSH jail enabled and configured
- [ ] Ban action set to UFW

### System updates
- [ ] System fully updated (`apt update && apt upgrade`)
- [ ] Unattended-upgrades enabled
- [ ] Security updates auto-install configured

### Services
- [ ] No unnecessary services running
- [ ] AppArmor enabled

### System limits
- [ ] File descriptor limit increased (65535)
- [ ] Process limit increased (65535)

### Quick audit script

```bash
#!/bin/bash
echo "=== SSH Config ==="
sudo sshd -T 2>/dev/null | grep -E "port |passwordauth|permitroot" || echo "sshd -T failed"

echo -e "\n=== Root SSH Keys ==="
if [ -s /root/.ssh/authorized_keys ]; then
  echo "WARNING: root has authorized_keys!"
  wc -l /root/.ssh/authorized_keys
else
  echo "OK: root authorized_keys empty/missing"
fi

echo -e "\n=== UFW Status ==="
sudo ufw status | head -10

echo -e "\n=== Fail2ban ==="
sudo fail2ban-client status sshd 2>/dev/null || echo "fail2ban not configured for sshd"

echo -e "\n=== Open Ports ==="
sudo ss -tlnp | grep LISTEN

echo -e "\n=== Auto Updates ==="
cat /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null || echo "not configured"

echo -e "\n=== AppArmor ==="
sudo aa-status 2>/dev/null | head -3 || echo "apparmor not available"

echo -e "\n=== File Limits ==="
ulimit -n
```

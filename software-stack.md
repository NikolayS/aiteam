# Software Stack for AI Agent VMs

This document describes the standard software stack for VMs running AI agents.

> **Note:** Always run `sudo apt update` before installing packages.

## Desktop environment

**Xfce4** is the standard desktop environment — lightweight, stable, and works well over VNC.

```bash
sudo apt install -y xfce4 xfce4-goodies dbus-x11
```

## Remote access (noVNC + Cloudflare)

### Architecture

```
Browser -> HTTPS -> Cloudflare (SSL termination) -> HTTPS -> VM:443 (Origin CA) -> nginx -> noVNC -> VNC
```

- **Cloudflare DNS**: A record pointing to VM IP (proxied/orange cloud)
- **Cloudflare SSL**: Full (Strict) mode with Origin CA certificate
- **nginx**: Reverse proxy with SSL and WebSocket support
- **noVNC**: Browser-based VNC client (works on mobile)
- **TigerVNC**: VNC server with virtual display

### Security features

- End-to-end encryption (browser <-> Cloudflare <-> VM)
- Port 443 restricted to Cloudflare IPs only via UFW
- VNC binds to localhost only
- **Cloudflare Access**: Email-based authentication (required)
- **VNC password**: Simple password as second layer

### Authentication layers

1. **Cloudflare Access** (first layer): Email-based authentication via one-time code
   - Configure an access policy to allow your team's email domain

2. **VNC password** (second layer): Simple password for the VNC session
   - Easy to type on phone (avoid complex special chars)
   - Mix letters, numbers, and simple symbols like `@` or `!`
   - Stored in `~/.vnc/passwd`

### Installation

#### 1. VNC server and noVNC

```bash
# VNC server
sudo apt install -y tigervnc-standalone-server tigervnc-common

# noVNC and websockify
sudo apt install -y novnc websockify

# nginx for SSL termination
sudo apt install -y nginx
```

#### 2. Set up VNC password

```bash
# Set VNC password (will prompt for password)
vncpasswd

# Or set non-interactively
echo "yourPassword" | vncpasswd -f > ~/.vnc/passwd
chmod 600 ~/.vnc/passwd
```

#### 3. Set up Cloudflare Access (email authentication)

Via Cloudflare API (requires token with `Access: Apps and Policies` permission):

```bash
CF_API_TOKEN="your-token"
CF_ACCOUNT_ID="your-account-id"
DOMAIN="hostname.example.com"
APP_NAME="Agent - hostname"

# Allow team email domain
ALLOWED_EMAILS='{"email": {"ends_with": "@example.com"}}'

# Create Access application
APP_ID=$(curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/access/apps" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{
    \"name\": \"${APP_NAME}\",
    \"domain\": \"${DOMAIN}\",
    \"type\": \"self_hosted\",
    \"session_duration\": \"24h\"
  }" | jq -r '.result.id')

# Create Access policy
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/access/apps/${APP_ID}/policies" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{
    \"name\": \"Allow authorized users\",
    \"decision\": \"allow\",
    \"include\": [${ALLOWED_EMAILS}]
  }"

echo "Cloudflare Access configured for ${DOMAIN}"
```

#### 4. Generate Cloudflare Origin CA certificate

Via Cloudflare API (requires token with Zone.SSL permission):

```bash
CF_API_TOKEN="your-token"
DOMAIN="hostname.example.com"

# Generate CSR and private key (ECDSA P-256 - faster than RSA, same security)
openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -keyout /tmp/${DOMAIN}.key \
  -out /tmp/${DOMAIN}.csr \
  -subj "/CN=${DOMAIN}"

# Request Origin CA certificate
CSR=$(cat /tmp/${DOMAIN}.csr)
curl -s -X POST 'https://api.cloudflare.com/client/v4/certificates' \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "$(jq -n --arg csr "$CSR" --arg host "$DOMAIN" '{
    hostnames: [$host],
    requested_validity: 5475,
    request_type: "origin-ecc",
    csr: $csr
  }')" | jq -r '.result.certificate' > /tmp/${DOMAIN}.crt

# Install certificates
sudo mkdir -p /etc/ssl/cloudflare
sudo mv /tmp/${DOMAIN}.crt /tmp/${DOMAIN}.key /etc/ssl/cloudflare/
sudo chmod 600 /etc/ssl/cloudflare/${DOMAIN}.key
```

#### 5. nginx configuration (`/etc/nginx/sites-available/novnc`)

This is a minimal plain-HTTP example for local testing. For production, use
the multi-agent SSL configuration below (or see the Cloudflare Origin CA
setup in step 4 above) and restrict port 443 to Cloudflare IPs via UFW.

```nginx
server {
    listen 6080;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:6081;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/novnc /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

#### 6. VNC and noVNC startup script (`/usr/local/bin/start-vnc.sh`)

```bash
#!/bin/bash
USER_NAME="${1:-agent}"
DISPLAY_NUM="${2:-1}"
VNC_PORT=$((5900 + DISPLAY_NUM))
WS_PORT=$((6080 + DISPLAY_NUM))

# Start VNC server
su - "$USER_NAME" -c "vncserver :$DISPLAY_NUM -geometry 1920x1080 -depth 24"

# Start websockify
websockify --web=/usr/share/novnc/ $WS_PORT localhost:$VNC_PORT &
```

---

## Multi-agent setup

Run multiple AI agents on a single VM using one Linux user with multiple VNC displays. This approach:
- Shares API keys (single install)
- Provides visual isolation via separate VNC displays
- Uses separate Chrome profiles per display

### Example layout

| Agent | Role | Display | Chrome Profile |
|-------|------|---------|----------------|
| Agent-1 | Primary assistant | :1 | `~/.config/chrome-agent1` |
| Agent-2 | Code reviewer | :2 | `~/.config/chrome-agent2` |
| Agent-3 | Research assistant | :3 | `~/.config/chrome-agent3` |

### Architecture

```
example.com/vnc.html         -> display :1 -> port 5901 -> websockify 6081 -> Agent-1
example.com/agent2/vnc.html  -> display :2 -> port 5902 -> websockify 6082 -> Agent-2
example.com/agent3/vnc.html  -> display :3 -> port 5903 -> websockify 6083 -> Agent-3
```

All displays owned by single admin user.

### Shared vs isolated

| Component | Shared | Isolated |
|-----------|--------|----------|
| AI tools | One install, one API key | — |
| Chrome | — | Separate `--user-data-dir` per display |
| VNC | — | Separate display per agent |
| File system | Same home dir | Subdirs per agent if needed |

### Add VNC display for an agent

Each agent gets its own VNC display. All run under the same admin user.

```bash
ADMIN_USER="agent"
AGENT_NAME="agent2"
DISPLAY_NUM="2"

# Create systemd service for this display
sudo bash -c "cat > /etc/systemd/system/vncserver@${DISPLAY_NUM}.service << EOF
[Unit]
Description=VNC Server for display ${DISPLAY_NUM} (${AGENT_NAME})
After=network.target

[Service]
Type=forking
User=${ADMIN_USER}
WorkingDirectory=/home/${ADMIN_USER}
ExecStart=/usr/bin/vncserver :${DISPLAY_NUM} -geometry 1920x1080 -depth 24 -localhost yes
ExecStop=/usr/bin/vncserver -kill :${DISPLAY_NUM}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF"

# Create websockify service
WS_PORT=$((6080 + DISPLAY_NUM))
VNC_PORT=$((5900 + DISPLAY_NUM))
sudo bash -c "cat > /etc/systemd/system/novnc-${AGENT_NAME}.service << EOF
[Unit]
Description=noVNC for ${AGENT_NAME}
After=vncserver@${DISPLAY_NUM}.service

[Service]
Type=simple
ExecStart=/usr/bin/websockify --web=/usr/share/novnc/ ${WS_PORT} localhost:${VNC_PORT}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF"

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now vncserver@${DISPLAY_NUM}
sudo systemctl enable --now novnc-${AGENT_NAME}
```

### Chrome with separate profiles

Launch Chrome with isolated profile per agent:

```bash
# In display :1 (Agent-1)
google-chrome --user-data-dir=~/.config/chrome-agent1

# In display :2 (Agent-2)
google-chrome --user-data-dir=~/.config/chrome-agent2

# In display :3 (Agent-3)
google-chrome --user-data-dir=~/.config/chrome-agent3
```

### nginx configuration for multi-agent

Update `/etc/nginx/sites-available/novnc`:

```nginx
server {
    listen 443 ssl http2;
    server_name agents.example.com;

    ssl_certificate /etc/ssl/cloudflare/agents.example.com.crt;
    ssl_certificate_key /etc/ssl/cloudflare/agents.example.com.key;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    # Agent 1 - primary (display :1)
    location / {
        proxy_pass http://127.0.0.1:6081;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }

    # Agent 2 (display :2)
    location /agent2/ {
        proxy_pass http://127.0.0.1:6082/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }

    # Agent 3 (display :3)
    location /agent3/ {
        proxy_pass http://127.0.0.1:6083/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

---

## Programming languages and runtimes

### Node.js (latest LTS via nvm)

```bash
# Install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash
source ~/.bashrc

# Install latest LTS
nvm install --lts
nvm use --lts
nvm alias default node
```

### Bun (latest)

```bash
curl -fsSL https://bun.sh/install | bash
source ~/.bashrc
```

### Python (latest via pyenv)

```bash
# Dependencies
sudo apt install -y build-essential libssl-dev zlib1g-dev \
  libbz2-dev libreadline-dev libsqlite3-dev curl git \
  libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev \
  libffi-dev liblzma-dev

# Install pyenv
curl https://pyenv.run | bash

# Add to shell
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc
echo 'eval "$(pyenv init -)"' >> ~/.bashrc
source ~/.bashrc

# Install latest Python
pyenv install 3.12
pyenv global 3.12
```

### Go (latest)

```bash
# Download latest
GO_VERSION=$(curl -s https://go.dev/VERSION?m=text | head -1)
wget "https://go.dev/dl/${GO_VERSION}.linux-amd64.tar.gz"
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf "${GO_VERSION}.linux-amd64.tar.gz"
rm "${GO_VERSION}.linux-amd64.tar.gz"

# Add to PATH
echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
source ~/.bashrc
```

### Ruby (latest via rbenv)

```bash
# Install rbenv
sudo apt install -y rbenv ruby-build

# Add to shell
echo 'eval "$(rbenv init -)"' >> ~/.bashrc
source ~/.bashrc

# Install latest Ruby
rbenv install 3.3.0
rbenv global 3.3.0
```

---

## Docker

```bash
# Install Docker and Docker Compose
curl -fsSL https://get.docker.com | sh

# Add user to docker group (no sudo needed for docker commands)
sudo usermod -aG docker $USER

# Enable Docker to start on boot
sudo systemctl enable docker
sudo systemctl enable containerd

# Verify
docker --version
docker compose version
```

---

## Build tools

```bash
# Essential build tools
sudo apt install -y \
  build-essential \
  cmake \
  pkg-config \
  autoconf \
  automake \
  libtool \
  git \
  curl \
  wget \
  jq \
  moreutils \
  unzip \
  zip

# Additional development libraries
sudo apt install -y \
  libssl-dev \
  libcurl4-openssl-dev \
  libpq-dev \
  libsqlite3-dev \
  libreadline-dev \
  zlib1g-dev
```

---

## AI agent tools

### Claude Code (latest)

```bash
# Install via npm (requires Node.js)
npm install -g @anthropic-ai/claude-code

# Or via Bun
bun install -g @anthropic-ai/claude-code
```

### Ollama (for local LLM access)

Ollama provides access to LLMs locally. Useful for privacy-sensitive tasks or offline use.

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a local model
ollama pull llama3.1:70b

# Test locally
echo "Hello" | ollama run llama3.1:70b
```

---

## CLI tools

### GitHub CLI (gh)

```bash
# Add GitHub CLI repository
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt update
sudo apt install -y gh
```

### GitLab CLI (glab)

```bash
# Download latest release
GLAB_VERSION=$(curl -s https://gitlab.com/api/v4/projects/34675721/releases | jq -r '.[0].tag_name' | sed 's/v//')
wget "https://gitlab.com/gitlab-org/cli/-/releases/v${GLAB_VERSION}/downloads/glab_${GLAB_VERSION}_Linux_x86_64.deb"
sudo dpkg -i "glab_${GLAB_VERSION}_Linux_x86_64.deb"
rm "glab_${GLAB_VERSION}_Linux_x86_64.deb"
```

---

## Browser

### Google Chrome

```bash
# Add Google Chrome repository
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt update
sudo apt install -y google-chrome-stable

# For headless/automated use
sudo apt install -y chromium-browser chromium-chromedriver
```

---

## Complete installation script

```bash
#!/bin/bash
set -e

echo "=== Installing software stack for AI agent VM ==="

# Update system
sudo apt update && sudo apt upgrade -y

# Build tools
sudo apt install -y build-essential cmake pkg-config autoconf automake libtool \
  git curl wget jq moreutils unzip zip libssl-dev libcurl4-openssl-dev libpq-dev \
  libsqlite3-dev libreadline-dev zlib1g-dev libbz2-dev libncursesw5-dev \
  xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev

# Desktop and VNC
sudo apt install -y xfce4 xfce4-goodies dbus-x11 tigervnc-standalone-server \
  tigervnc-common novnc websockify nginx

# Chrome
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt update && sudo apt install -y google-chrome-stable

# Node.js via nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm install --lts

# Bun
curl -fsSL https://bun.sh/install | bash

# Go
GO_VERSION=$(curl -s https://go.dev/VERSION?m=text | head -1)
wget "https://go.dev/dl/${GO_VERSION}.linux-amd64.tar.gz"
sudo tar -C /usr/local -xzf "${GO_VERSION}.linux-amd64.tar.gz"
rm "${GO_VERSION}.linux-amd64.tar.gz"
echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc

# Python via pyenv
curl https://pyenv.run | bash
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
pyenv install 3.12
pyenv global 3.12

# Ruby via rbenv
sudo apt install -y rbenv ruby-build
eval "$(rbenv init -)"
rbenv install 3.3.0
rbenv global 3.3.0

# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
sudo systemctl enable docker containerd

# GitHub CLI
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list
sudo apt update && sudo apt install -y gh

# GitLab CLI
GLAB_VERSION=$(curl -s https://gitlab.com/api/v4/projects/34675721/releases | jq -r '.[0].tag_name' | sed 's/v//')
wget "https://gitlab.com/gitlab-org/cli/-/releases/v${GLAB_VERSION}/downloads/glab_${GLAB_VERSION}_Linux_x86_64.deb"
sudo dpkg -i "glab_${GLAB_VERSION}_Linux_x86_64.deb"
rm "glab_${GLAB_VERSION}_Linux_x86_64.deb"

# Claude Code
npm install -g @anthropic-ai/claude-code

echo "=== Installation complete ==="
echo "Next steps:"
echo "1. Set up VNC password: vncpasswd"
echo "2. Configure Cloudflare Access for your domain"
echo "3. Set up nginx with SSL"
```

---

## Services autostart

All essential services must be enabled to survive reboots:

```bash
# Enable all required services
sudo systemctl enable docker
sudo systemctl enable containerd
sudo systemctl enable nginx
sudo systemctl enable vncserver@1
sudo systemctl enable novnc
sudo systemctl enable fail2ban
sudo systemctl enable ufw

# Verify enabled services
systemctl is-enabled docker nginx vncserver@1 novnc fail2ban
```

### Verify after reboot

```bash
# Check all services are running
systemctl status docker nginx vncserver@1 novnc fail2ban --no-pager
```

---

## Version management

All version-managed tools should be kept up to date:

| Tool | Update command |
|------|----------------|
| Node.js | `nvm install --lts && nvm use --lts` |
| Bun | `bun upgrade` |
| Python | `pyenv install <version> && pyenv global <version>` |
| Go | Re-download from go.dev |
| Ruby | `rbenv install <version> && rbenv global <version>` |
| Claude Code | `npm update -g @anthropic-ai/claude-code` |
| gh | `sudo apt update && sudo apt upgrade gh` |
| glab | Re-download latest .deb |

---

## Software stack audit checklist

Use this checklist to verify all software is properly installed:

### Remote access
- [ ] Desktop environment installed (Xfce)
- [ ] VNC server installed and configured
- [ ] VNC password set
- [ ] noVNC/websockify installed
- [ ] nginx configured with SSL
- [ ] Origin CA certificate installed
- [ ] VNC systemd service running
- [ ] noVNC systemd service running

### Programming languages
- [ ] Node.js (LTS) via nvm
- [ ] Bun installed
- [ ] Python (latest) via pyenv
- [ ] Go (latest)
- [ ] Ruby (latest) via rbenv

### Docker
- [ ] Docker installed
- [ ] Docker Compose installed
- [ ] User added to docker group
- [ ] Docker service enabled for autostart

### Build tools
- [ ] build-essential installed
- [ ] cmake, pkg-config installed
- [ ] Development libraries (libssl-dev, libpq-dev, etc.)

### AI tools
- [ ] Claude Code installed

### CLI tools
- [ ] GitHub CLI (gh) installed
- [ ] GitLab CLI (glab) installed

### Browser
- [ ] Chrome installed

### Quick verification script

```bash
#!/bin/bash
echo "=== Remote Access ==="
systemctl is-active vncserver@1 && echo "VNC running" || echo "VNC not running"
systemctl is-active novnc && echo "noVNC running" || echo "noVNC not running"
systemctl is-active nginx && echo "nginx running" || echo "nginx not running"

echo -e "\n=== Languages ==="
node --version 2>/dev/null && echo "Node.js OK" || echo "Node.js missing"
bun --version 2>/dev/null && echo "Bun OK" || echo "Bun missing"
python3 --version 2>/dev/null && echo "Python OK" || echo "Python missing"
go version 2>/dev/null && echo "Go OK" || echo "Go missing"
ruby --version 2>/dev/null && echo "Ruby OK" || echo "Ruby missing"

echo -e "\n=== AI Tools ==="
which claude 2>/dev/null && echo "Claude Code OK" || echo "Claude Code missing"

echo -e "\n=== CLI Tools ==="
gh --version 2>/dev/null | head -1 && echo "gh OK" || echo "gh missing"
glab --version 2>/dev/null | head -1 && echo "glab OK" || echo "glab missing"

echo -e "\n=== Browser ==="
google-chrome --version 2>/dev/null && echo "Chrome OK" || echo "Chrome missing"
```

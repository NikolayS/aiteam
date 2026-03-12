# Quickstart: From Zero to Your First AI Agent

This guide walks you through setting up your first autonomous AI software engineer — from account registration to a running agent you can chat with via Telegram, WhatsApp, or Slack.

**Time estimate:** ~1–2 hours for first-time setup.

---

## Phase 1: Register Accounts

### 0. GitHub

Sign up at [github.com](https://github.com) if you don't already have an account. Your AI agent will use GitHub for code hosting and collaboration.

### 1. AI Provider (choose one or both)

You need API access to at least one LLM provider:

| Provider | Sign up | Notes |
|----------|---------|-------|
| **Anthropic (Claude)** | [console.anthropic.com](https://console.anthropic.com) | Recommended for Claude Code |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) | Alternative / additional provider |

**Option A — API key:** Create an API key from the provider's dashboard. You'll need this later.

**Option B — OAuth (Claude only):** If you plan to use Claude Code with OAuth login (via browser), you can skip the API key for now. Note: OAuth requires a browser session on the machine where Claude Code runs, which may need extra setup on a remote VM.

### 2. Domain Name (optional but recommended)

A domain lets you access your agent's desktop via `https://agent.yourdomain.com` instead of raw IPs.

Register at any registrar:
- [Namecheap](https://namecheap.com)
- [GoDaddy](https://godaddy.com)
- [101domains](https://101domains.com)
- [Cloudflare Registrar](https://dash.cloudflare.com) (can register directly here)

### 3. Cloudflare

Sign up at [dash.cloudflare.com](https://dash.cloudflare.com). The **free plan** is sufficient.

If you registered a domain elsewhere, add it to Cloudflare and update your registrar's nameservers to point to Cloudflare. This gives you:
- Free SSL/TLS
- DDoS protection
- Cloudflare Access (email-based auth for your agent's VNC)

### 4. Cloud Provider

You need a cloud provider to run VMs. Pick one:

| Provider | Starting price | Regions | Sign up |
|----------|---------------|---------|---------|
| **Hetzner** | ~$7/mo for 4 vCPU | US (Ashburn, Hillsboro), EU | [hetzner.com](https://hetzner.com) |
| **AWS** | ~$35/mo for comparable | Global | [aws.amazon.com](https://aws.amazon.com) |
| **GCP** | ~$30/mo for comparable | Global | [cloud.google.com](https://cloud.google.com) |
| **DigitalOcean** | ~$24/mo for 4 vCPU | US, EU, Asia | [digitalocean.com](https://digitalocean.com) |

After signing up, generate an **API key/token** from the provider's dashboard. You'll need it to provision VMs programmatically.

- **Hetzner:** Go to your project → Security → API Tokens → Generate API Token (read/write)
- **AWS:** IAM → Users → Create access key
- **GCP:** IAM → Service Accounts → Create key (JSON)

### 5. Optional Services

Depending on your agent's capabilities, you may want:

| Service | Purpose | Sign up |
|---------|---------|---------|
| **Resend** or **SendGrid** | Sending emails | [resend.com](https://resend.com) / [sendgrid.com](https://sendgrid.com) |
| **Twilio** | SMS and phone calls | [twilio.com](https://twilio.com) |
| **ElevenLabs** | Voice synthesis | [elevenlabs.io](https://elevenlabs.io) |

These are not required to get started — add them later as needed.

---

## Phase 2: Install Claude Code Locally

Claude Code runs on your local machine first. You'll use it to provision and configure everything else.

### Install

```bash
# Requires Node.js 18+
npm install -g @anthropic-ai/claude-code
```

### Authenticate

**Option A — OAuth (recommended for getting started):**
```bash
claude
# This opens a browser window for authentication
# Log in with your Anthropic account
```

**Option B — API key:**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
claude
```

### Plan size

- **Regular plan ($20/month):** Fine to get started. You may hit rate limits during heavy provisioning.
- **Larger plan ($100–200/month):** Recommended if you plan to run agents continuously. Higher rate limits and more capacity.

---

## Phase 3: Provision Your First VM

Now you'll use Claude Code to set up a cloud VM. This is where your AI agent will live.

### Generate SSH keys

Start Claude Code and ask it to generate an SSH key pair:

```bash
claude --dangerously-skip-permissions
```

> **WARNING:** The `--dangerously-skip-permissions` flag gives Claude Code full access to run any command on your machine without asking for confirmation. This is convenient for automated provisioning but means Claude Code can read, write, and execute anything your user account can. **Use with caution.** Make sure you understand what commands are being run. You can omit this flag and approve each command manually if you prefer safety over speed.

Once inside Claude Code, ask:

```
Generate an SSH key pair for my AI agent VM. Use ed25519. Save it to ~/.ssh/ai-agent
and don't set a passphrase.
```

### Provision the VM

Next, ask Claude Code to create a VM. Example prompt for Hetzner:

```
I have a Hetzner API token: <YOUR_TOKEN>

Create a VM with these specs:
- Name: ai-agent-1
- Location: ash (Ashburn, US)
- Type: cpx31 (4 vCPU, 8GB RAM) or cx32 (4 vCPU, 16GB RAM) -- pick the cheapest
  with at least 4 vCPUs
- Image: Ubuntu 24.04
- SSH key: use the public key from ~/.ssh/ai-agent.pub
- Enable a static/permanent IP

After creation, harden the server following security best practices:
- Change SSH to port 2223
- Disable root login and password auth
- Set up UFW firewall: allow only SSH (2223) and HTTPS (443)
- Install and configure fail2ban
- Set up unattended security updates
- Create a non-root user called "agent" with sudo access

Then verify I can SSH in as "agent" on port 2223.
```

Claude Code will execute the necessary API calls and SSH commands to set up your VM.

### Set up VNC (remote desktop)

Once the VM is provisioned, ask Claude Code:

```
On the VM ai-agent-1, set up remote desktop access:
1. Install Xfce4 desktop environment
2. Install TigerVNC and noVNC
3. Set the VNC password to: <CHOOSE_YOUR_PASSWORD>
   (use something easy to type on mobile, like "MyAgent1!")
4. Set up nginx as reverse proxy with SSL
5. Configure systemd services for auto-start
6. Install Google Chrome browser

Use the security setup from infrastructure.md as reference.
```

### Connect via VNC

Once setup is complete, you can access your agent's desktop in a browser:

1. **If you set up Cloudflare + domain:**
   - Point your domain (e.g., `agent.yourdomain.com`) to the VM's IP via Cloudflare (proxied)
   - Set up Cloudflare Access for email-based auth
   - Visit `https://agent.yourdomain.com/vnc.html`

2. **If using IP directly (quick test):**
   - First, open the firewall for noVNC: `sudo ufw allow 6081/tcp comment 'noVNC'`
   - Open `http://<VM_IP>:6081/vnc.html` in your browser
   - Enter your VNC password
   - **Warning:** This exposes noVNC without encryption. Use only for quick testing, then remove the rule with `sudo ufw delete allow 6081/tcp`.

You should see an Xfce desktop. This is your agent's workspace.

---

## Phase 4: Install OpenClaw on the VM

OpenClaw is an agent framework that gives your AI agent persistent identity and connects it to messaging platforms.

### Install

SSH into your VM and install OpenClaw:

```bash
ssh -i ~/.ssh/ai-agent -p 2223 agent@<VM_IP>
```

Then on the VM:

```bash
# Install Node.js via nvm (see software-stack.md for details)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash
source ~/.bashrc
nvm install --lts
nvm alias default node

# Install OpenClaw
npm install -g openclaw
```

Or — even better — ask Claude Code to do it:

```
SSH into my VM at <VM_IP> (port 2223, user "agent", key ~/.ssh/ai-agent)
and install OpenClaw. Also install Claude Code on the VM.
Give OpenClaw full permissions to the VM.
```

### Configure OpenClaw

Create the configuration file on the VM:

```bash
mkdir -p ~/.openclaw
cat > ~/.openclaw/openclaw.json << 'EOF'
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "anthropic/claude-sonnet-4-20250514"
      }
    }
  },
  "env": {
    "ANTHROPIC_API_KEY": "sk-ant-..."
  }
}
EOF
```

---

## Phase 5: Connect a Messaging Channel

OpenClaw supports multiple messaging platforms. Choose one and follow the setup below.

### Option A: Telegram

1. **Create a Telegram bot:**
   - Open Telegram and message [@BotFather](https://t.me/BotFather)
   - Send `/newbot`
   - Choose a name (e.g., "My AI Engineer")
   - Choose a username (e.g., `my_ai_engineer_bot`)
   - Copy the **bot token** BotFather gives you

2. **Configure OpenClaw:**
   Add to your `~/.openclaw/openclaw.json`:
   ```json
   {
     "channels": {
       "telegram": {
         "botToken": "YOUR_TELEGRAM_BOT_TOKEN"
       }
     }
   }
   ```

3. **Start OpenClaw:**
   ```bash
   openclaw start
   ```

4. **Test it:** Open Telegram, find your bot, and send it a message.

### Option B: WhatsApp

WhatsApp integration requires a business API setup:

1. **Create a Meta Developer account:** Go to [developers.facebook.com](https://developers.facebook.com)
2. **Create an app** with WhatsApp product enabled
3. **Get credentials:**
   - WhatsApp Business Account ID
   - Phone Number ID
   - Permanent access token

4. **Configure OpenClaw:**
   ```json
   {
     "channels": {
       "whatsapp": {
         "accessToken": "YOUR_WHATSAPP_TOKEN",
         "phoneNumberId": "YOUR_PHONE_NUMBER_ID",
         "webhookVerifyToken": "a-random-string-you-choose"
       }
     }
   }
   ```

5. **Set up webhook:** WhatsApp needs a public HTTPS endpoint. If your VM has a domain:
   - Configure the webhook URL in Meta Developer dashboard: `https://agent.yourdomain.com/whatsapp/webhook`
   - Make sure nginx proxies this path to OpenClaw

6. **Start OpenClaw:**
   ```bash
   openclaw start
   ```

### Option C: Slack

1. **Create a Slack App:**
   - Go to [api.slack.com/apps](https://api.slack.com/apps)
   - Click "Create New App" → "From scratch"
   - Name it (e.g., "AI Engineer") and pick your workspace

2. **Configure permissions:**
   - Go to "OAuth & Permissions"
   - Add Bot Token Scopes: `chat:write`, `channels:history`, `channels:read`, `im:history`, `im:read`, `im:write`
   - Install the app to your workspace
   - Copy the **Bot User OAuth Token** (`xoxb-...`)

3. **Enable Events:**
   - Go to "Event Subscriptions" → Enable
   - Set Request URL to: `https://agent.yourdomain.com/slack/events`
   - Subscribe to bot events: `message.channels`, `message.im`

4. **Configure OpenClaw:**
   ```json
   {
     "channels": {
       "slack": {
         "botToken": "xoxb-YOUR-TOKEN",
         "signingSecret": "YOUR_SIGNING_SECRET",
         "appToken": "xapp-YOUR-APP-TOKEN"
       }
     }
   }
   ```

5. **Start OpenClaw:**
   ```bash
   openclaw start
   ```

6. **Test it:** Invite the bot to a channel or DM it directly.

---

## Summary

After completing all phases, you have:

```
Your laptop                         Cloud VM
┌──────────────┐                   ┌──────────────────────────┐
│ Claude Code  │ ── SSH (2223) ──> │ Ubuntu 24.04             │
│ (provisioner)│                   │ ├── OpenClaw (agent)     │
└──────────────┘                   │ ├── Claude Code          │
                                   │ ├── Chrome + VNC Desktop │
Your browser                       │ └── Firewall (SSH+HTTPS) │
┌──────────────┐                   └──────────────────────────┘
│ VNC viewer   │ ── HTTPS (443) ──>        │
└──────────────┘                           │
                                           │
Telegram / WhatsApp / Slack ◄──────────────┘
```

**What's next:**
- Customize your agent's identity (see [ai-engineer-identity.md](ai-engineer-identity.md))
- Add more agents on the same VM (see multi-agent setup in [software-stack.md](software-stack.md))
- Set up monitoring with the [dashboard](dashboard/README.md)
- Connect additional services (email, voice, etc.)

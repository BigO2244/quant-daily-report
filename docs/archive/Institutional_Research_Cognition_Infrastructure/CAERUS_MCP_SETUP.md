# Caerus MCP Server — Setup & Test Guide

## 1. Deploy to GCP VM

SSH into your VM (`alpha-stack-490922`) and:

```bash
# Install the MCP SDK
pip install "mcp[cli]" --break-system-packages
# Or in your existing venv:
# pip install "mcp[cli]"

# Copy the server script to your VM (scp, git, or paste)
# Place it alongside your Caerus code, e.g. ~/caerus/caerus_mcp_server.py

# Set the base directory (adjust to your actual layout)
export CAERUS_BASE=~/caerus

# Test it runs
python caerus_mcp_server.py
# Should print: "Starting Caerus MCP server on 0.0.0.0:8765"
```

## 2. Update paths

The server assumes this directory structure:
```
~/caerus/
├── output/
│   ├── scores/    ← JSON scoring output files
│   └── digests/   ← HTML digest files
├── logs/          ← Pipeline log files
└── caerus_mcp_server.py
```

If your layout differs, either:
- Set the `CAERUS_BASE` env var, or
- Edit the path constants at the top of the script

## 3. Open the firewall

Allow TCP 8765 on your GCP VM:

```bash
gcloud compute firewall-rules create allow-mcp-server \
    --allow tcp:8765 \
    --source-ranges=YOUR_HOME_IP/32 \
    --target-tags=YOUR_VM_TAG \
    --description="MCP server access (restricted to home IP)"
```

**Important:** Lock this to your home IP. Don't open to 0.0.0.0/0.
Get your IP: `curl ifconfig.me`

## 4. Connect from Claude Code

On your laptop, add the MCP server to your Claude Code config.

**Option A: Project-level config** (`.mcp.json` in project root):
```json
{
  "mcpServers": {
    "caerus": {
      "type": "url",
      "url": "http://YOUR_VM_EXTERNAL_IP:8765/mcp"
    }
  }
}
```

**Option B: Global config** (`~/.claude/settings.json`):
```json
{
  "mcpServers": {
    "caerus": {
      "type": "url",
      "url": "http://YOUR_VM_EXTERNAL_IP:8765/mcp"
    }
  }
}
```

Then in Claude Code:
```
claude
> /mcp          # Should show "caerus" as connected
> What's the status of my Caerus pipeline?
> Show me the latest scoring output
> List recent digest files
```

## 5. Test with the MCP Inspector (optional)

The SDK ships a dev inspector for debugging:

```bash
# On the VM
mcp dev caerus_mcp_server.py
```

This opens a browser-based tool to invoke each tool interactively
and inspect the request/response payloads.

## 6. Run as a background service (optional)

Once it's working, keep it running with systemd or tmux:

**tmux (quick):**
```bash
tmux new -s mcp
export CAERUS_BASE=~/caerus
python caerus_mcp_server.py
# Ctrl+B, D to detach
```

**systemd (production):**
```ini
# /etc/systemd/system/caerus-mcp.service
[Unit]
Description=Caerus MCP Server
After=network.target

[Service]
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/caerus
Environment=CAERUS_BASE=/home/YOUR_USER/caerus
ExecStart=/usr/bin/python3 /home/YOUR_USER/caerus/caerus_mcp_server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now caerus-mcp
sudo systemctl status caerus-mcp
```

## What's next

Once this works end-to-end, natural extensions:
- **trigger_ingestion** tool — kick off a pipeline run on-demand
- **get_portfolio_status** — pull current paper trading positions
- **search_scores_by_ticker** — query historical scores for a symbol
- Add auth (OAuth or API key header) before opening beyond your home IP

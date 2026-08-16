# Deploy On The Road to a GCP VM

## Prerequisites

- A GCP VM (e.g. e2-small, Debian/Ubuntu) with Docker installed
- Tailscale installed on both the VM and your phone (for private-network access)
- Your Hermes `~/.hermes` directory (auth.json with Nous Portal creds, config.yaml with provider: nous / model: tencent/hy3)

## Steps

### 1. Clone the repo on the VM

```bash
git clone https://github.com/smallTechOrg/on-the-road.git
cd on-the-road
git checkout feature/on-the-road-20260816-1849-v0.1
```

### 2. Copy your Hermes config to the VM

From your Mac:
```bash
# Only copy config + auth, never the full agent codebase
scp ~/.hermes/config.yaml ~/.hermes/auth.json <vm>:~/.hermes/
```

Or if Hermes is already installed on the VM, just ensure `~/.hermes/config.yaml` has:
```yaml
model:
  provider: nous
  max_tokens: 4096
  default: tencent/hy3
```

### 3. Create .env on the VM

```bash
cat > .env << 'EOF'
ONTHEROAD_TOKEN=<your-token-32+-chars>
EOF
```

### 4. Build and run

```bash
docker build -t ontheroad:latest .
docker run -d --name ontheroad \
  --restart unless-stopped \
  -p 8100:8100 \
  --env-file .env \
  -v $HOME/.hermes:/root/.hermes \
  -v $(pwd)/data:/data \
  ontheroad:latest
```

### 5. Verify

```bash
curl localhost:8100/healthz
# {"status":"ok","version":"0.1.0"}
```

### 6. Access from your phone

Over Tailscale: `http://<vm-tailscale-ip>:8100`

The Hermes agent subprocess needs the full `~/.hermes/hermes-agent` directory to be present inside the container. If you only copied config/auth, Hermes sessions won't start — you'll need either:
- Volume-mount the full `~/.hermes` (includes hermes-agent): `-v $HOME/.hermes:/root/.hermes`
- Or install Hermes on the VM first (`curl -fsSL https://hermes.nousresearch.com/install | bash`) then the mount picks it up

### Port forwarding (alternative to Tailscale)

If not using Tailscale, you can SSH tunnel:
```bash
ssh -L 8100:localhost:8100 <vm>
```
Then access at `http://localhost:8100` on your Mac, or set up a GCP firewall rule for port 8100 (not recommended without auth hardening).

# Bootstrap

Bringing MP2 up on a Windows host from nothing. Written against DEV-01 (Windows 10 Pro
19045, Intel i7-4770, 23.94 GiB RAM, AMD RX 580) but the steps are not specific to it.

Windows stays the host. **Linux is the application environment, and nothing MP2 needs is
installed into Windows.**

## 0. Prerequisites

- Windows 10 21H2+ or Windows 11, with virtualization usable.
- A volume with plenty of free space that is **not** the system volume. On DEV-01 this is
  D:. Docker images, WSL disks, model weights and corpus data all live there.
- Git for Windows (for cloning; the repository can live on any volume).

Check what you have:

```powershell
wsl --version
wsl --list --verbose
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
  Select-Object DeviceID, FileSystem,
    @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}},
    @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}}
```

## 1. WSL2 and Ubuntu 24.04

If WSL is absent, install it. This needs administrator rights and **a reboot**:

```powershell
wsl --install --no-distribution     # run as Administrator
# reboot here
wsl --install -d Ubuntu-24.04
wsl --set-default-version 2
```

If a suitable distribution already exists, reuse it rather than creating another.

### Put the distro on a large volume

A WSL distro's virtual disk defaults to `%LOCALAPPDATA%` on C:. On DEV-01 that filled C:
to 0.21 GiB free and corrupted a Docker layer. Move it:

```powershell
wsl --shutdown
wsl --manage Ubuntu-24.04 --move D:\WSL\Ubuntu-24.04
```

Non-destructive, reversible, no administrator rights, ~80 seconds for 9 GB. Verify:

```powershell
Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss |
  ForEach-Object { $p = Get-ItemProperty $_.PSPath; "$($p.DistributionName) -> $($p.BasePath)" }
```

### Resource policy

Create `%USERPROFILE%\.wslconfig`. Sizing for a 16–24 GiB host: roughly 10–12 GiB to WSL,
most but not all threads, a real swap file, and **no idle timeout** — without the last one
WSL shuts the VM down between commands and kills every running container.

```ini
[wsl2]
memory=12GB
processors=6
swap=4GB
swapFile=D:\\WSL\\swap.vhdx
vmIdleTimeout=-1

[experimental]
sparseVhd=true
```

Apply it:

```powershell
wsl --shutdown
```

Confirm inside Linux with `free -h` and `nproc`.

## 2. Inside Ubuntu

```bash
wsl -d Ubuntu-24.04
```

Enable systemd if it is not already running (`/etc/wsl.conf`), then `wsl --shutdown` and
reopen:

```ini
[boot]
systemd=true
```

```bash
systemctl is-system-running      # expect: running (or degraded)
```

Install prerequisites and Docker Engine from Ubuntu's own packages — **not** Docker
Desktop, which must not become a product dependency:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 python3-venv python3-pip git ca-certificates
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

The group change needs a new session:

```powershell
wsl --terminate Ubuntu-24.04
```

Verify without sudo:

```bash
docker --version && docker compose version && docker info >/dev/null && echo docker ok
```

### Git identity

Not configured automatically — MP2 will not guess an identity. Set it if you intend to
commit from Linux:

```bash
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"
```

## 3. The repository

If the repository already exists on a Windows volume, use it in place. On DEV-01:

```bash
cd "/mnt/d/GitHub/MP2 - Media Personality Projections"
```

Otherwise clone it (private repository; authentication required):

```bash
mkdir -p ~/src && cd ~/src
git clone https://github.com/waywardtech/MP2-Media-Personality-Projections.git mp2
cd mp2
```

> Working from `/mnt/d` is convenient and was used throughout, but 9p is slow. If builds
> feel I/O-bound, a clone on the Linux filesystem (`~/src/mp2`) is materially faster.

Create the environment file. `.env` is gitignored and must never be committed:

```bash
cp .env.example .env
```

The defaults are development-only placeholders and reach only loopback-bound services.
Replace every one of them before any non-local deployment.

## 4. Build and start

```bash
cd infra/compose
docker compose --profile core build      # first build is slow: ffmpeg, OpenCV, librosa, Whisper
cd ../..
infra/scripts/mp2.sh up
```

The first build downloads the Whisper `tiny` weights (~75 MB, MIT) into the image so the
runtime never needs network access. To build with no model weights:

```bash
docker build -f infra/containers/python.Dockerfile --build-arg MP2_WHISPER_REPO= -t mp2-api .
```

`mp2.sh up` waits for health and prints status. Expect all six core services healthy.

## 5. Migrate and verify

```bash
infra/scripts/mp2.sh migrate
infra/scripts/mp2.sh psql -c '\dt'

curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/ready
```

You should see 20 canonical tables plus `alembic_version`, and both endpoints returning ok.

## 6. Vertical slice

```bash
infra/scripts/mp2.sh fixture          # synthetic AV, no speech
infra/scripts/mp2.sh speech-fixture   # synthetic AV with speech, exercises ASR
```

Then register and analyze — see OPERATIONS.md for the full curl sequence, or use the
console:

```bash
infra/scripts/mp2.sh up-ui            # http://127.0.0.1:5173
```

## 7. Tests

```bash
infra/scripts/mp2.sh test
infra/scripts/mp2.sh lint
```

The suite runs inside the runtime image. Roughly three minutes on DEV-01.

## Troubleshooting

Real failures seen during this bootstrap, with fixes, are in TROUBLESHOOTING.md. The two
most likely:

- **Everything exits at once with status 0** — WSL shut the VM down. Set `vmIdleTimeout=-1`.
- **`connection refused` to an IP nothing is listening on** — stale container networking
  after a WSL restart. `mp2.sh down && mp2.sh up`.

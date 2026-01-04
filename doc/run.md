# MikuInvidious Minimal Guide (Unsafe)

This tutorial aims to configure the MikuInvidious ASGI Server in a local environment for "testing". For production environments like adding reverse proxy to your setup, please refer to `compose.yml`.

## 1. Prepare Environment

### Recommended Environment

- **Python 3.11+**
- **Redis**: Used for Session and data caching.

### Install Environment and Redis

Please refer to the [Redis installation documentation](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/install-redis-on-linux/)

```bash
sudo apt install python3 python3-venv git
```

---

## 2. Quick Start

### Download Code and Create Environment

```bash
git clone https://github.com/apicalshark/mikuinvidious.git
cd mikuinvidious

# Create venv
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 3. Minimal Configuration

Copy the sample configuration:

```bash
cp config.toml.sample config.toml
```

**Key configurations for development:**

- **`[proxy]`**:
  - You can add `proxy_url` to set your SOCKS5/HTTP proxy (e.g., cf warp, `http://127.0.0.1:1080`).

---

## 4. Run the project

Execute in the project root directory:

```bash
# Set Python path and start
python3 python/main.py
```

After starting, access `http://localhost:8888`.

---

## Tips

- **Authentication**: Should be avoided, but if you need to debug 1080P or premium features, please fill in your Bilibili Cookies in the `[credential]` section of `config.toml` and set `use_cred` to `true`.

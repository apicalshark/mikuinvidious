# Local Installation Guide (No Docker)

This guide provides step-by-step instructions for setting up and running the MikuInvidious application on your local machine without using Docker.

This approach is intended for users who cannot or do not want to use Docker. It provides the most direct control over the environment but requires manual installation and configuration of all system dependencies.

## Prerequisites

Before you begin, ensure you have the following installed and running:

- **Python 3.10+**
- **Node.js v18+ and npm** (only for development)
- **Git**
- **uv** (the python package manager `pip install uv`)
- **Redis**
- **Cloudflare WARP Desktop Client**

---

## Step 1: Install and Configure Dependencies

The application requires two background services to be running: Redis and the Cloudflare WARP proxy.

### 1. Install and Run Redis

Redis is required for caching and session storage.
Please follow the official [Redis installation guide](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/).

Verify that Redis is running on its default port, `6379`.

### 2. Install and Configure Cloudflare WARP

The application requires the Cloudflare WARP client to act as a SOCKS5 proxy to access Bilibili content.

1. **Install the WARP Client:**
   Download and install the official client for your operating system from the [Cloudflare 1.1.1.1 website](https://1.1.1.1/).

2. **Enable Local Proxy Mode:**
   - Open the WARP client's **Preferences** or **Settings** panel.
   - Navigate to the **Advanced** tab.
   - Click **Configure Proxy**.
   - Check the box to **Enable local proxy**.
   - Set the **Port** to `1080`.
   - Save the changes.
   - Go back to the main WARP settings screen and select the new **"WARP via Local Proxy"** mode.

Your WARP client is now listening for SOCKS5 connections on port `1080`.

---

## Step 2: Set Up the Application

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **Install Dependencies:**

   ```bash
   # Creates the environment
   uv sync
   ```

3. **Build TailwindCSS(for development only):**

   ```bash
   npm run build:css
   ```

---

## Step 3: Configure the Application

Create and edit a local configuration file to connect the application to your manually configured services.

1. **Create `config.toml`:**

   ```bash
   cp config.toml.sample config.toml
   ```

2. **Edit `config.toml`:**
   Open the file and make the following changes:
   - Under `[server]`, set a unique `secret_key`. Generate one with:
     `python -c 'import secrets; print(secrets.token_hex(16))'`

   - Under `[redis]`, ensure the `url` points to your local Redis instance:

     ```toml
     url = "redis://localhost:6379"
     ```

   - Under `[proxy]`, ensure `proxy_url` point to your WARP client:

     ```toml
     proxy_url = "socks5://localhost:1080"
     ```

---

## Step 4: Run the Application

With your dependencies running and your configuration set, start the server:

```bash
uv run python/main.py
```

The application will be available at **`http://localhost:8888`** (the default port in `config.toml.sample`).

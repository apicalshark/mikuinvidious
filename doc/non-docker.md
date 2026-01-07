# Local Development Guide (Non-Docker App)

This guide provides step-by-step instructions for setting up and running the MikuInvidious Python application directly on your local machine.

This "hybrid" approach is for developers who want to work on the Python code outside of Docker, offering faster iteration and easier debugging. However, it still uses Docker to run the essential **Redis** and **Warp** (SOCKS5 proxy) services, which are required for full application functionality.

## Prerequisites

Before you begin, ensure you have the following installed:
- **Python 3.10+**
- **Node.js v18+ and npm**
- **Git**
- **Docker** (to run dependency services)
- **uv** (the python package manager `pip install uv`)

---

## Step 1: Start Required Services (Redis & Warp)

The application requires Redis for caching/sessions and Cloudflare Warp for proxying requests to Bilibili. The easiest way to run these is with Docker.

1.  **Run Redis:**
    Open a terminal and run the following command to start a Redis container.
    ```bash
    docker run --rm -d --name miku_redis -p 6379:6379 redis:alpine
    ```

2.  **Run Warp SOCKS5 Proxy:**
    In another terminal, run the command below to start the `warp` proxy. This allows the application to access Bilibili content.
    ```bash
    docker run --rm -d --name miku_warp -p 1080:1080 \
      --cap-add=NET_ADMIN --cap-add=MKNOD --cap-add=AUDIT_WRITE \
      --device /dev/net/tun:/dev/net/tun \
      caomingjun/warp
    ```
    *Note: The `--cap-add` and `--device` flags are necessary for the proxy to function correctly.*

You should now have two containers (`miku_redis`, `miku_warp`) running. You can verify this with `docker ps`.

---

## Step 2: Set Up the Application

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/apicalshark/mikuinvidious
    cd mikuinvidious
    ```

2.  **Set Up a Python Virtual Environment:**
    ```bash
    # Create the virtual environment
    python -m venv venv

    # Activate it (macOS/Linux)
    source venv/bin/activate
    # Or activate it (Windows)
    .\\venv\\Scripts\\activate
    ```

3.  **Install Dependencies:**
    Install both Python and Node.js dependencies.
    ```bash
    # Install Python packages
    uv sync

    # Install Node.js packages
    npm install
    ```

4.  **Build Frontend Assets:**
    Compile the Tailwind CSS.
    ```bash
    npm run build:css
    ```

---

## Step 3: Configure the Application

The application needs to know how to connect to the Redis and Warp services you started earlier.

1.  **Create a Configuration File:**
    Copy the sample file to create your local configuration.
    ```bash
    cp config.toml.sample config.toml
    ```

2.  **Edit `config.toml`:**
    Open the `config.toml` file and make the following critical changes:

    -   In the `[server]` section, set a unique `secret_key`. You can generate one with:
        `python -c 'import secrets; print(secrets.token_hex(16))'`

    -   In the `[redis]` section, ensure the `url` points to your local Redis container:
        ```toml
        [redis]
        url = "redis://127.0.0.1:6379"
        ```

    -   In the `[proxy]` section, configure the HTTP and HTTPS proxies to point to your local Warp container:
        ```toml
        [proxy]
        http_proxy = "socks5://127.0.0.1:1080"
        https_proxy = "socks5://127.0.0.1:1080"
        ```

---

## Step 4: Run the Application

With the services running and the configuration set, you can now start the Quart server.

```bash
uv run python/main.py
```

The application will be available at **`http://localhost:8888`** (the default port specified in `config.toml.sample`).

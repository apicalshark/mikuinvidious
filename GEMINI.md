# MikuInvidious Project Overview

MikuInvidious is a free and open-source frontend for Bilibili, inspired by Invidious. It aims to provide a lightweight, privacy-focused experience for browsing Bilibili content without the need for heavy official clients or extensive tracking.

## Core Technologies
- **Language:** Python 3
- **Web Framework:** [Flask](https://flask.palletsprojects.com/) (with async support)
- **Web Server & Proxy:** [Twisted](https://twistedmatrix.com/) (used for efficient reverse proxying and serving the WSGI application)
- **Database/Cache:** [Redis](https://redis.io/) (required for caching video URLs, session management, and credential storage)
- **API Wrapper:** [bilibili-api-python](https://github.com/nemo2011/bilibili-api)
- **Templating:** Jinja2 (with theme support)

## System Architecture

### High-Level Design
The system uses **Twisted** as the primary web server to handle high-concurrency media streaming and proxying, while wrapping a **Flask** application to handle business logic and UI rendering.

*   **Entry Point (`main.py`):** Starts a Twisted reactor that exposes:
    *   **Reverse Proxy (`/proxy`):** Handles video and image streaming directly using asynchronous `httpx` streams. This bypasses CORS/Referer checks and region blocks.
    *   **WSGI Container:** Serves the Flask application for all other routes.
*   **Network Transport:** Integrates with **Cloudflare WARP** (via SOCKS5) to route traffic to Bilibili, ensuring access even from restricted networks.

### Request Flowchart

```mermaid
graph TD
    User((User / Browser))
    
    subgraph "Docker Host"
        Twisted[Twisted Web Server<br>(main.py:8888)]
        
        subgraph "MikuInvidious App"
            Router{URL Path?}
            ProxyRes["ReverseProxyResource<br>(Async Stream)"]
            WSGIRes["WSGIResource<br>(Flask App)"]
            Views["Flask Views<br>(views.py/app.py)"]
            BiliAPI["Bilibili API Wrapper"]
        end
        
        Redis[(Redis Cache)]
    end
    
    subgraph "Network Services"
        Warp["Cloudflare WARP<br>(SOCKS5 Proxy)"]
    end
    
    subgraph "External"
        BiliCDN["Bilibili CDN<br>(Videos/Images)"]
        BiliServers["Bilibili API Servers"]
    end

    %% Flows
    User --> Twisted
    Twisted --> Router
    
    %% Proxy Path
    Router -- "/proxy/..." --> ProxyRes
    ProxyRes -- "Check Cache" --> Redis
    ProxyRes -- "Stream Content" --> Warp
    
    %% App Path
    Router -- "Other Routes" --> WSGIRes
    WSGIRes --> Views
    Views -- "Get Metadata" --> BiliAPI
    BiliAPI -- "Fetch Data" --> Warp
    
    %% External Connections
    Warp --> BiliCDN
    Warp --> BiliServers
    
    %% Returns
    BiliCDN -.-> Warp -.-> ProxyRes -.-> Twisted -.-> User
    BiliServers -.-> Warp -.-> BiliAPI -.-> Views -.-> WSGIRes -.-> Twisted -.-> User
```

### Component Breakdown
- **Application Logic:** 
    - `app.py`: Initializes the Flask app, error handlers, and basic routes (login, logout, b23.tv redirection).
    - `views.py`: Main routing logic for home, search, video, space, and author views.
    - `shared.py`: Configuration loading (`config.toml`), Redis connection, and utility functions.
    - `proxy.py`: Flask Blueprint for fallback proxying (though Twisted's native proxy is preferred).
    - `danmaku.py`: Fetches and converts Bilibili danmaku (XML to JSON).
    - `extra.py`: Utilities for article-to-HTML conversion and AV/BV ID manipulation.
    - `refresher.py`: Utility to refresh Bilibili credentials.

## Deep Analysis & Architectural Insights

### 1. Structural Integrity & Core Patterns
*   **Twisted-Flask Bridge:** The project uses an advanced integration where `asyncioreactor` bridges Twisted's event loop with Python's `asyncio`. This allows the high-performance networking of Twisted to coexist with the modern async Bilibili API wrapper and `httpx`.
*   **Performance-First Proxying:** Media streams (video/images) bypass the Flask/WSGI stack entirely. They are handled by a native Twisted `ReverseProxyResource` in `main.py`, enabling efficient, non-blocking chunked transfers directly from Bilibili's CDNs.
*   **Dynamic Theming:** The architecture supports multiple frontends. The logic in `shared.py` dynamically selects templates based on cookies or URL parameters, allowing the site to serve different interfaces (e.g., `modern` vs. `wayback`) from the same backend logic.

### 2. Theme Comparison
| Feature | **Modern** (Default) | **Wayback** |
| :--- | :--- | :--- |
| **Framework** | Tailwind CSS | Pure.css |
| **UX Design** | Material You / Dark Mode | Classic Web / Minimalist |
| **Responsiveness** | Mobile-first, fluid layout | Grid-based, structured |

### 3. Codebase Health & Observations
*   **Strengths:** Excellent separation of concerns between proxying (network layer) and view logic (application layer). Robust caching via Redis significantly reduces the load on Bilibili's API and improves latency.
*   **Optimization Potential:** 
    *   **Logic Density:** `views.py` contains dense data mapping logic. This could be moved to a dedicated service layer or data transformers.
    *   **Template Shared Logic:** Shared UI components could be extracted into Jinja2 macros to reduce duplication between theme folders.
    *   **Proxy Refinement:** The `ReverseProxyResource` could be hardened to better handle complex HTTP Range requests for improved seeking performance.

## Infrastructure (Docker)

The production infrastructure consists of three orchestrated services defined in `compose.yml`:

| Service | Image | Description |
| :--- | :--- | :--- |
| **`app`** | *(Local Build)* | The main Python application. Exposes port `8000`. |
| **`redis`** | `redis:alpine` | Persists sessions and caches API responses. |
| **`warp`** | `caomingjun/warp` | SOCKS5 proxy (port `1080`) for routing traffic to Bilibili. |

## Configuration

Configuration is managed via `config.toml` (recommended) or Environment Variables.

*   **`[site]`**: Metadata (Name, URL) and Robots policy.
*   **`[twisted]`**: Server host/port settings.
*   **`[credential]`**: Bilibili cookies (`SESSDATA`, `bili_jct`, etc.) for authenticated access.
*   **`[proxy]`**: 
    *   `use_proxy = true`: Proxies all media through the server (required for most regions).
    *   `use_proxy = false`: Redirects to Akamai mirrors where possible (Direct Mode).
*   **`[redis]`**: Redis connection details.

## Development & Deployment

### Docker Deployment (Recommended)
1.  **Configure Environment:**
    Ensure `compose.yml` environment variables match your needs (especially `SITE_URL` and `FLASK_SECRET_KEY`).
2.  **Run with Docker Compose:**
    ```bash
    docker-compose up -d --build
    ```
3.  **Access:** `http://localhost:8000`

### Manual Development Setup
1.  **Prerequisites:** Python 3.8+, Redis server running, `pandoc` (optional).
2.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Configure:**
    ```bash
    cp config.toml.sample config.toml
    # Edit config.toml with Redis details
    ```
4.  **Run:**
    ```bash
    python main.py
    ```
    Access at `http://[::]:8888` (or configured port).

## Development Conventions
- **License:** GNU GPL-3.0.
- **Theming:** Templates in `templates/themes/`. Default is `modern`.
- **Static Assets:** `static/` contains customized `video.js` and `danmaku.js`.
- **Async:** Use `async def` for all view functions performing I/O.
- **Proxying Strategy:**
    - **Images:** Always proxied.
    - **Videos:** Proxied if `use_proxy=true`. If `false`, only Akamai mirrors are allowed via 302 Redirects to prevent dead links.
- **B23.tv:** Short links are resolved server-side.
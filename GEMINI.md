# MikuInvidious Project Overview

MikuInvidious is a free and open-source frontend for Bilibili, inspired by Invidious. It aims to provide a lightweight, privacy-focused experience for browsing Bilibili content without the need for heavy official clients or extensive tracking.

## Core Technologies
- **Language:** Python 3
- **Web Framework:** [Quart](https://pgjones.gitlab.io/quart/) (Modern asynchronous web framework)
- **Web Server:** [NGINX Unit](https://unit.nginx.org/) (High-performance application server)
- **Database/Cache:** [Redis](https://redis.io/) (required for caching video URLs, session management, and credential storage)
- **API Wrapper:** [bilibili-api-python](https://github.com/nemo2011/bilibili-api)
- **Templating:** Jinja2 (with theme support)

## System Architecture

### High-Level Design
The system uses **NGINX Unit** as the primary application server to handle Quart (ASGI) application. All logic and proxying are handled within the Quart application using asynchronous I/O.

*   **Entry Point (`main.py`):** A simple Quart entry point for local development.
*   **App Logic (`app.py`):** Main entry point for NGINX Unit, registering blueprints and routes.
*   **Reverse Proxy (`proxy.py`):** Handles video and image streaming using Quart's `stream_with_context` and `httpx`.
*   **Network Transport:** Integrates with **Cloudflare WARP** (via SOCKS5) to route traffic to Bilibili.

### Request Flowchart

```mermaid
graph TD
    User((User / Browser))
    
    subgraph "Docker Host"
        Unit[NGINX Unit Server<br>(Port 8000)]
        
        subgraph "MikuInvidious App"
            Router{URL Path?}
            ProxyRes["Quart Proxy Blueprint<br>(Async Stream)"]
            Views["Quart Views<br>(views.py/app.py)"]
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
    User --> Unit
    Unit --> Router
    
    %% Proxy Path
    Router -- "/proxy/..." --> ProxyRes
    ProxyRes -- "Check Cache" --> Redis
    ProxyRes -- "Stream Content" --> Warp
    
    %% App Path
    Router -- "Other Routes" --> Views
    Views -- "Get Metadata" --> BiliAPI
    BiliAPI -- "Fetch Data" --> Warp
    
    %% External Connections
    Warp --> BiliCDN
    Warp --> BiliServers
    
    %% Returns
    BiliCDN -.-> Warp -.-> ProxyRes -.-> Unit -.-> User
    BiliServers -.-> Warp -.-> BiliAPI -.-> Views -.-> Unit -.-> User
```

### Component Breakdown
- **Application Logic:** 
    - `app.py`: Initializes the Quart app, error handlers, and basic routes.
    - `views.py`: Main routing logic for home, search, video, space, and author views.
    - `shared.py`: Configuration loading (`config.toml`), Redis connection, and Quart app initialization.
    - `proxy.py`: Quart Blueprint for media proxying.
    - `danmaku.py`: Fetches and converts Bilibili danmaku.
    - `extra.py`: Utilities for article-to-HTML conversion and ID manipulation.
    - `refresher.py`: Utility to refresh Bilibili credentials.

## Deep Analysis & Architectural Insights

### 1. Structural Integrity & Core Patterns
*   **Quart Framework:** The project leverages Quart for its async capabilities.
*   **Unified Proxying:** Media streams (video/images) are handled via Quart blueprints, allowing for consistent application-level control and session management.
*   **Dynamic Theming:** Templates are dynamically selected based on cookies or URL parameters.

### 2. Theme Comparison
| Feature | **Modern** (Default) | **Wayback** |
| :--- | :--- | :--- |
| **Framework** | Tailwind CSS | Pure.css |
| **UX Design** | Material You / Dark Mode | Classic Web / Minimalist |
| **Responsiveness** | Mobile-first, fluid layout | Grid-based, structured |

### 3. Codebase Health & Observations
*   **Strengths:** Clear async implementation using Quart and `httpx`. Robust caching via Redis.
*   **Optimization Potential:** 
    *   **Logic Density:** `views.py` could benefit from further decoupling of data transformation logic.
    *   **Streaming Efficiency:** Quart's `stream_with_context` works well, but high-concurrency streaming might benefit from further tuning in NGINX Unit's process management.

## Infrastructure (Docker)

The production infrastructure consists of three orchestrated services defined in `compose.yml`:

| Service | Image | Description |
| :--- | :--- | :--- |
| **`app`** | *(Local Build)* | NGINX Unit running the Quart application. Exposes port `8000`. |
| **`redis`** | `redis:alpine` | Persists sessions and caches API responses. |
| **`warp`** | `caomingjun/warp` | SOCKS5 proxy (port `1080`) for routing traffic to Bilibili. |

## Configuration

Configuration is managed via `config.toml` (recommended) or Environment Variables.

*   **`[site]`**: Metadata and Robots policy.
*   **`[server]`**: Host and port settings.
*   **`[credential]`**: Bilibili cookies for authenticated access.
*   **`[proxy]`**: Proxy settings for media streams.
*   **`[redis]`**: Redis connection details.

## Development & Deployment

### Docker Deployment (Recommended)
1.  **Run with Docker Compose:**
    ```bash
    docker-compose up -d --build
    ```
3.  **Access:** `http://localhost:8000`

### Manual Development Setup
1.  **Prerequisites:** Python 3.8+, Redis server running.
2.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Run:**
    ```bash
    python main.py
    ```
    Access at `http://localhost:8888` (or configured port).

## Recent Updates (Phase 1-3)
- **Live Streaming:** Full support for browsing and watching Bilibili Live rooms (`/live`, `/live/<room_id>`).
- **Watch History:** Anonymous, local watch history stored in Redis (`/history`).
- **Refactoring:** 
    - Decoupled API logic using `transformers.py`.
    - Unified video player logic with `templates/macros.html`.
    - Flattened template data structures for better maintainability.

## Development Conventions
- **License:** GNU GPL-3.0.
- **Theming:** Templates in `templates/themes/`. Default is `modern`.
- **Static Assets:** `static/` contains customized `video.js` and `danmaku.js`.
- **Async:** Use `async def` for all view functions performing I/O.
- **Proxying Strategy:**
    - **Images:** Always proxied.
    - **Videos:** Proxied if `use_proxy=true`. If `false`, only Akamai mirrors are allowed via 302 Redirects to prevent dead links.
- **B23.tv:** Short links are resolved server-side.
# MikuInvidious Project Overview

MikuInvidious is a free and open-source frontend for Bilibili, inspired by Invidious. It aims to provide a lightweight, privacy-focused experience for browsing Bilibili content without the need for heavy official clients or extensive tracking.

## Core Technologies
- **Language:** Python 3
- **Web Framework:** [Flask](https://flask.palletsprojects.com/) (with async support)
- **Web Server & Proxy:** [Twisted](https://twistedmatrix.com/) (used for efficient reverse proxying and serving the WSGI application)
- **Database/Cache:** [Redis](https://redis.io/) (required for caching video URLs, session management, and credential storage)
- **API Wrapper:** [bilibili-api-python](https://github.com/nemo2011/bilibili-api)
- **Templating:** Jinja2 (with theme support)

## Architecture
- **Entry Point:** `main.py` starts a Twisted reactor that handles:
    - **Custom Reverse Proxy:** Efficient reverse proxying for video and image streams via a custom `ReverseProxyResource`. This implementation includes a built-in SOCKS5 client to support proxying via SOCKS5 tunnels (configured via `HTTP_PROXY`).
    - **Flask Integration:** Routing all other requests to the Flask application via `WSGIResource`.
- **Application Logic:** 
    - `app.py`: Initializes the Flask app, error handlers, and some basic routes (login, logout, b23.tv redirection, robots.txt).
    - `views.py`: Contains the main routing logic for home, search, video, space, and author views.
    - `shared.py`: Handles configuration loading (environment variables + `config.toml`), Redis connection, and common utility functions like theme detection and template rendering.
    - `proxy.py`: Contains a Flask Blueprint for proxying. While Twisted's native proxy in `main.py` is preferred for performance and SOCKS5 support, this Blueprint provides a fallback implementation.
    - `danmaku.py`: Handles fetching and converting Bilibili danmaku (XML to JSON) for the frontend.
    - `res.py`: Handles danmaku resource fetching routes.
    - `filters.py`: Contains Jinja2 custom filters for data formatting (date, intsep, secdur, pic).
    - `extra.py`: Provides Bilibili-specific utilities, including article-to-HTML conversion (using BeautifulSoup and optionally Pandoc) and AV/BV ID conversions.
    - `refresher.py`: A utility script to refresh Bilibili credentials and update `config.toml`.
- **Configuration:** Managed via `config.toml` (recommended) or environment variables. Credentials refreshed by `refresher.py` are stored under the `[updatedcred]` section in `config.toml`.

## Building and Running

### Prerequisites
- Python 3.8+
- Redis server running
- `pandoc` (optional, for some article conversion features)

### Setup
1.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
2.  **Configure the application:**
    Copy the sample configuration file and edit it with your settings (Redis host, port, etc.).
    ```bash
    cp config.toml.sample config.toml
    ```
3.  **Run the application:**
    ```bash
    python main.py
    ```
    The application will be available at the host and port specified in `config.toml` (default is `http://[::]:8888`).

## Development Conventions
- **License:** GNU GPL-3.0.
- **Theming:** Templates are organized under `templates/themes/`. The default theme is `wayback`. Theme detection is handled in `shared.py`.
- **Static Assets:** Located in `static/`, including customized versions of `video.js` and `danmaku.js`.
- **Async:** The project uses `flask[async]` and `asyncio` for non-blocking Bilibili API calls. Always use `async def` for view functions that perform I/O.
- **Proxying:** Image proxying is always enabled for reliability. Video proxying depends on the `use_proxy` setting in `config.toml` (or `NO_PROXY` environment variable).
    - **Direct Mode (`use_proxy = false`):** **Only** Akamai mirrors are allowed (via 302 Redirect). All other video mirrors are blocked with a 403 error to prevent non-working direct connections.
    - **Proxy Mode (`use_proxy = true`):** All video and image traffic is proxied through the server.
    - **Note:** The scraper prioritizes Akamai mirrors to maximize availability in Direct Mode. Ensure Redis is configured for caching stream URLs.
- **B23.tv Redirection:** The application handles `b23.tv` short links by resolving them server-side and redirecting to the corresponding video or article view.


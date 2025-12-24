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
    - Efficient reverse proxying for video and image streams via `ReverseProxyResource`.
    - Routing all other requests to the Flask application via `WSGIResource`.
- **Application Logic:** 
    - `app.py`: Initializes the Flask app, error handlers, and some basic routes.
    - `views.py`: Contains the main routing logic for home, search, video, space, and author views.
    - `shared.py`: Handles configuration loading, Redis connection, and common utility functions like theme detection.
    - `proxy.py`: Contains a Flask Blueprint for proxying, though primarily used as a fallback or for non-Twisted environments (Twisted's native proxy in `main.py` is preferred for performance).
    - `danmaku.py`: Handles fetching and converting Bilibili danmaku (comments).
- **Configuration:** Managed via `config.toml` (recommended) or environment variables.

## Building and Running

### Prerequisites
- Python 3.8+
- Redis server running

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
- **Theming:** Templates are organized under `templates/themes/`. The default theme is `wayback`.
- **Static Assets:** Located in `static/`, including customized versions of `video.js` and `danmaku.js`.
- **Async:** The project uses `flask[async]` and `asyncio` for non-blocking Bilibili API calls. Always use `async def` for view functions that perform I/O.
- **Proxying:** Image proxying is always enabled to ensure reliability. Video proxying depends on the `NO_PROXY` environment variable. If `NO_PROXY` is set to `1` or `true` (Direct Mode), the server will only perform a `302 Redirect` for Akamai mirrors (which allow direct connection); all other video mirrors will be proxied to avoid 403 Forbidden errors. Ensure Redis is correctly configured for caching stream URLs.

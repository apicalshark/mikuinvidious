# Local Installation (Non-Docker)

This guide provides step-by-step instructions for setting up and running MikuInvidious on your local machine without using Docker. This method is recommended for users who want to minimize memory usage or prefer managing their development environment directly.

## Why Choose a Non-Docker Setup?
- **Lower Memory Footprint:** Avoids the overhead of running a Docker daemon and containers.
- **Direct Environment Control:** Allows for direct management of Python and Node.js versions and dependencies.
- **Easier Debugging:** Simplifies debugging by running the application directly on your host machine.

## Prerequisites
Before you begin, ensure you have the following installed:
- **Python 3.10+:** [Download Python](https://www.python.org/downloads/)
- **Node.js v18+ and npm:** [Download Node.js](https://nodejs.org/)
- **Git:** [Download Git](https://git-scm.com/downloads/)
- **Redis:** A running Redis instance is required for session and cache storage. [Install Redis](https://redis.io/docs/getting-started/installation/).

---

## Step-by-Step Installation

### 1. Clone the Repository
First, clone the MikuInvidious repository to your local machine.
```bash
git clone https://github.com/apicalshark/mikuinvidious
cd mikuinvidious
```

### 2. Set Up a Python Virtual Environment
Using a virtual environment is highly recommended to isolate project dependencies.
```bash
# Create the virtual environment
python -m venv venv

# Activate the virtual environment
# On macOS and Linux:
source venv/bin/activate
# On Windows:
.\\venv\\Scripts\\activate
```
**Note:** You must activate the virtual environment in your terminal session before proceeding.

### 3. Install Backend Dependencies
With the virtual environment activated, install the required Python packages.
```bash
pip install -r requirements.txt
```

### 4. Install Frontend Dependencies
Next, install the Node.js packages needed for the frontend.
```bash
npm install
```

### 5. Build Frontend Assets
Compile the Tailwind CSS to generate the final stylesheet.
```bash
npm run build:css
```

### 6. Configure the Application
Create a configuration file from the sample provided.
```bash
cp config.toml.sample config.toml
```
Now, open `config.toml` and customize the settings. At a minimum, you should set a `QUART_SECRET_KEY`. You can generate one with the following command:
```bash
python -c 'import secrets; print(secrets.token_hex(16))'
```
Update the `secret_key` field in the `[server]` section of `config.toml` with the generated key.

### 7. Run the Application
Finally, start the Quart server.
```bash
python python/main.py
```
The application will be running at `http://localhost:8000` (or the port you configured).

---

## Troubleshooting

- **`command not found: python` or `pip`:** Ensure Python is installed and its location is in your system's `PATH`.
- **`'venv' is not recognized`:** Make sure you are using a supported version of Python 3. If the issue persists, you may need to install the `virtualenv` package manually (`pip install virtualenv`).
- **Permissions Errors with `npm install`:** You may need to run the command with `sudo` or fix your npm permissions. See the [npm documentation](https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally) for guidance.

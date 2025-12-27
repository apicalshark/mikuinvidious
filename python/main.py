# Copyright (C) 2023 MikuInvidious Team
# 
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
# 
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.

import asyncio
from hypercorn.config import Config
from hypercorn.asyncio import serve

from shared import app, appconf
from app import * # Ensure all routes are registered

async def main():
    config = Config()
    # Bind to 0.0.0.0 to allow cross-container communication
    config.bind = ["0.0.0.0:8080"]
    config.accesslog = "-"
    config.errorlog = "-"
    
    print(f"Starting MikuInvidious (ASGI) on {config.bind[0]}")
    await serve(app, config)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

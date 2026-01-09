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

# Ensure all app components are loaded
import app as app_module  # noqa: F401

# Import the ASGI application instance
from shared import app, close_global_client

# Register the shutdown hook. The ASGI server (Granian) will handle the
# lifespan protocol, triggering this function on shutdown.
app.after_serving(close_global_client)

# The 'app' object is now exposed for the ASGI server to import and run.
# The command will be something like:
# granian python.main:app

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

import toml, redis

from flask import request, render_template, Flask
from flask_caching import Cache
from bilibili_api import Credential

from refresher import renew_cookies

try:
	appconf = toml.load('config.toml')
except FileNotFoundError:
	print('Configuration file not found, maybe you forgot to copy `config.toml.sample\' to `config.toml\'?')

# Connect to our nice redis database.
appredis = redis.Redis(**appconf['redis'])

# Initialize the flask app.
app = Flask('app')
app.config.from_mapping(appconf['flask'])

# And also configure the flask_cache module.
appcache = Cache(app, config={'CACHE_TYPE': 'RedisCache'})

# Initilize credentials for bilibili API.
if appconf['credential']['use_cred']:
    credstore = appconf['updatedcred'] if 'updatedcred' in appconf else \
        appconf['credential']
    appcred = Credential(sessdata=credstore['sessdata'],
                         bili_jct=credstore['bili_jct'],
                         buvid3=credstore['buvid3'],
                         dedeuserid=credstore['dedeuserid'],
                         ac_time_value=credstore['ac_time_value'])

    if renew_cookies(appcred):
        appconf = toml.load('config.toml')
        credstore = appconf['updatedcred']
        appcred = Credential(sessdata=credstore['sessdata'],
                             bili_jct=credstore['bili_jct'],
                             buvid3=credstore['buvid3'],
                             dedeuserid=credstore['dedeuserid'],
                             ac_time_value=credstore['ac_time_value'])
else:
    appcred = None

##########################################
# Util functions
##########################################

def detect_theme():
    """Determine the theme of the users' request."""
    if theme := request.args.get('theme'):
        return theme
    elif theme := request.cookies.get('theme'):
        return theme
    else:
        return 'default'

async def render_template_with_theme(fp, **kwargs):
    """Render a template with theming support."""
    t = detect_theme()

    if dark_theme := request.cookies.get('dark-theme'):
        dark_theme = int(dark_theme)
    else:
        dark_theme = False

    if t == 'default':
        t = appconf['display']['default_theme']
    return render_template(f'themes/{t}/{fp}', dark_mode=dark_theme,
                           **appconf['site'],
                           **kwargs)

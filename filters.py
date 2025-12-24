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

from shared import app, appconf

from datetime import datetime, timedelta

# Convert a timestamp to a humand readable date format.
@app.template_filter('date')
def _jinja2_filter_datetime(ts, fmt='%Y年%m月%d日 %H点%m分'):
	return datetime.fromtimestamp(ts).strftime(fmt)

# Convert a integer to the one with separator like 1,000,000.
@app.template_filter('intsep')
def _jinja2_filter_intsep(i):
        return f'{int(i):,}'

# Convert a duration in seconds to human readable duration.
@app.template_filter('secdur')
def __jinja2_filter_secdur(delta_t):
        return str(timedelta(seconds=int(delta_t)))

# Convert a url of a photo asset to MikuInvidious proxy url.
@app.template_filter('pic')
def __jinja2_filter_pic(url):
        if appconf['proxy']['use_proxy']:        
                return '/proxy/pic/' + url.split('//')[1]
        else:
                return url.replace('http://', 'https://')

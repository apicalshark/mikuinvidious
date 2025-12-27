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

'''Server-Side danmaku translation'''

from xml.dom import minidom

def danmaku_xml_conv(domtree):
	return list(map(danmaku_elem_conv, domtree.getElementsByTagName('d')))

def danmaku_elem_conv(d):
	p = d.getAttribute('p').split(',')

	try:
		m = ({ '6': 'ltr', '1': 'rtl', '5': 'top', '4': 'bottom' })[p[1]]
	except:
		return {}
                
	ftsize = int(p[2]) or 25
	ftcolor = hex(int(p[3]))[2:]

	return {
		'text': d.firstChild.data,
		'mode': m,
		'time': float(p[0]),
		'style': {
			'fontSize': f'{ftsize}px',
			'color': f'#{ftcolor}',
			'textShadow': '-1px -1px #fff, -1px 1px #fff, 1px -1px #fff, 1px 1px #fff' \
				if ftcolor == '000000' else '-1px -1px #000, -1px 1px #000, 1px -1px #000, 1px 1px #000',
			'font': f'{ftsize}px sans-serif',
			'fillStyle': f'#{ftcolor}',
			'strokeStyle': '#fff' if ftcolor == '000000' else '#000',
			'lineWidth': 2.0,
		}
	}

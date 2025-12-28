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

import toml
from bilibili_api import Credential, sync


def discard_generated_data(fn):
    with open(fn) as file:
        lines = file.readlines()

    index = None
    for i, line in enumerate(lines):
        if "## GENERATED DATA, DO NOT WRITE ANYTHING BELOW!!! ##\n" in line:
            index = i
            break

    if index is not None:
        with open(fn, "w") as file:
            file.writelines(lines[:index])
            return True
    else:
        return False


def renew_cookies(cred):
    if sync(cred.check_refresh()):
        sync(cred.refresh())
        write_cookies(cred)
        print("Cookies refreshed.")
        return True
    return False


def write_cookies(cred):
    buf = "" if discard_generated_data("config.toml") else "\n\n"
    buf += "## GENERATED DATA, DO NOT WRITE ANYTHING BELOW!!! ##\n"
    buf += "[updatedcred]\n"
    for k, v in cred.get_cookies().items():
        buf += f"{k.lower()} = '{v}'\n"

    with open("config.toml", "a") as f:
        f.write(buf)


if __name__ == "__main__":
    print("Trying to refresh the cookies...")
    appconf = toml.load("config.toml")
    credstore = appconf["updatedcred"] if "updatedcred" in appconf else appconf["credential"]
    appcred = Credential(
        sessdata=credstore["sessdata"],
        bili_jct=credstore["bili_jct"],
        buvid3=credstore["buvid3"],
        dedeuserid=credstore["dedeuserid"],
        ac_time_value=credstore["ac_time_value"],
    )

    if renew_cookies(appcred):
        print("Successfully refreshed the cookies.")
    else:
        print("Cookies are already up-to-date.")

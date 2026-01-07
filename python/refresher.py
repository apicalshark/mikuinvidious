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


def renew_cookies(cred):
    try:
        if sync(cred.check_refresh()):
            sync(cred.refresh())
            print("Cookies refreshed successfully.")
            print("\n" + "="*50)
            print("!! CRITICAL SECURITY WARNING !!")
            print("Do NOT save these credentials directly in config.toml.")
            print("Update your environment variables or secrets management system.")
            print("="*50 + "\n")
            print("Refreshed Credentials:\n")
            for k, v in cred.get_cookies().items():
                env_var = k.upper()
                print(f"  {env_var}='{v}'")
            print("\n" + "="*50)
            print("Example: export SESSDATA='...'")
            print("="*50 + "\n")
            return True
    except Exception as e:
        print(f"Error refreshing cookies: {e}")
    return False


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

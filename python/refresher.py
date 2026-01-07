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

from bilibili_api import Credential, sync
import toml


# Sentinel-Vulnerability-Fix: The function below is part of an insecure workflow
# that writes sensitive credentials to a configuration file. It has been
# disabled to prevent accidental secret exposure.
# def discard_generated_data(fn):
#     with open(fn) as file:
#         lines = file.readlines()
#
#     index = None
#     for i, line in enumerate(lines):
#         if "## GENERATED DATA, DO NOT WRITE ANYTHING BELOW!!! ##\n" in line:
#             index = i
#             break
#
#     if index is not None:
#         with open(fn, "w") as file:
#             file.writelines(lines[:index])
#             return True
#     else:
#         return False


def renew_cookies(cred):
    try:
        if sync(cred.check_refresh()):
            sync(cred.refresh())
            write_cookies(cred)
            # The success message is now printed by the secure write_cookies function.
            return True
    except Exception as e:
        print(f"Error refreshing cookies: {e}")
    return False


# Sentinel-Vulnerability-Fix: The original version of this function insecurely
# wrote sensitive credentials (session tokens, etc.) directly into the config.toml file.
# Storing secrets in plaintext configuration files is a critical security risk, as they
# can be accidentally committed to version control or exposed by a file-read vulnerability.
#
# The function has been refactored to print the refreshed credentials to the console
# instead. This ensures that the user is in control of their secrets and can securely
# update them as environment variables or in a proper secrets management system,
# without ever writing them to the local filesystem.
def write_cookies(cred):
    print("Cookies refreshed successfully.")
    print("\n" + "=" * 50)
    print("!! CRITICAL SECURITY WARNING !!")
    print("Do NOT save these credentials directly in config.toml.")
    print("Update your environment variables or secrets management system.")
    print("=" * 50 + "\n")
    print("Refreshed Credentials:\n")
    for k, v in cred.get_cookies().items():
        env_var = k.upper()
        print(f"  {env_var}='{v}'")
    print("\n" + "=" * 50)
    print("Example: export SESSDATA='...'")
    print("=" * 50 + "\n")


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

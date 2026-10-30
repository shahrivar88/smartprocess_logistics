"""The display switches the Desk needs before it draws a Link query.

A Link query is set synchronously when a form opens, so the browser cannot ask the server
whether to override one: the switch has to be there already. A boot value is one read per
session with no extra request, and it is the standard place for exactly this kind of
configuration.

The payload is seven site-wide booleans and nothing else: no stock, no company, no user, and
no access decision. Every read a switch controls still runs its own permission checks.
"""

from smartprocess_logistics.warehouse import visibility_settings


def boot_session(bootinfo):
	bootinfo.smartprocess_logistics = visibility_settings.load()

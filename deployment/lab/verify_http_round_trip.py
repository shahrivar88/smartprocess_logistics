"""Drive the deployed workflow through the front end, exactly as the browser does.

This exists because verifying a *file* is not verifying a *runtime*. An earlier check fetched
`/assets/smartprocess_logistics/js/logistics_settings_transfer.js`, found the fix in it, and
reported success — while the browser went on sending a request without a token, because the Desk
does not load that file at all: `doctype_js` is inlined into the form's meta as `__js`
(`frappe/desk/form/meta.py:111`) and the meta is served from a cache. The file was current; the
meta was not; the browser executed the meta.

So this script asks the running deployment the same three questions the browser asks, over HTTP,
through the front end:

    login                    POST /api/method/login
    the form's JavaScript    GET  /api/method/frappe.desk.form.load.getdoctype
    the workflow itself      POST export_serials → preview_serial_restore → apply_serial_restore

It prints status codes and markers only. The administrator password is read from the environment
and is never printed; the session cookie never leaves the process.

Run it inside the lab's backend container:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -e LAB_ADMIN_PASSWORD="$(...) " backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_http_round_trip.py
"""

import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("SP_LAB_BASE", "http://frontend:8080")
SITE = os.environ.get("SP_LAB_SITE", "sp_logistics_lab")
USER = os.environ.get("SP_LAB_USER", "Administrator")

# The two markers that tell the corrected JavaScript from the one a browser was executing.
FIXED_JS_MARKER = "response.message) || {}"
STALE_JS_MARKER = "callback: (response) => response.message"


class Recorder:
	def __init__(self):
		self.failures = 0

	def check(self, condition, description, detail=""):
		print(f"  {'ok  ' if condition else 'FAIL'} {description}")
		if not condition:
			self.failures += 1
			if detail:
				print(f"       {detail}")

		return condition


def opener():
	jar = http.cookiejar.CookieJar()

	return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(agent, path, data=None, query=None, timeout=120):
	"""One request through the front end, with the site named the way a browser's Host does."""
	url = BASE + path

	if query:
		url += "?" + urllib.parse.urlencode(query)

	body = urllib.parse.urlencode(data).encode() if data is not None else None
	request = urllib.request.Request(
		url,
		data=body,
		headers={
			"Host": SITE,
			"X-Frappe-Site-Name": SITE,
			"Content-Type": "application/x-www-form-urlencoded",
			"Accept": "application/json",
		},
	)

	try:
		with agent.open(request, timeout=timeout) as response:
			return response.status, response.read().decode("utf-8", "replace")
	except urllib.error.HTTPError as error:
		return error.code, error.read().decode("utf-8", "replace")


def message_of(text):
	try:
		payload = json.loads(text)
	except ValueError:
		return None

	return payload.get("message")


def refusal_of(text):
	"""The server's own sentence, without the JSON wrapper Frappe puts it in."""
	payload = None

	try:
		payload = json.loads(text)
	except ValueError:
		return text[:160]

	server_messages = payload.get("_server_messages")

	if server_messages:
		try:
			first = json.loads(server_messages)[0]
			return json.loads(first).get("message", "")[:160]
		except (ValueError, IndexError, TypeError):
			pass

	return f"{payload.get('exc_type', 'error')}: {str(payload.get('exception', ''))[:120]}"


def main():
	recorder = Recorder()
	password = os.environ.get("LAB_ADMIN_PASSWORD", "")

	if not password:
		print("refused: LAB_ADMIN_PASSWORD is not set in the environment")
		return 2

	agent = opener()

	print(f"deployment under test: {BASE}  (site {SITE})")

	status, text = call(agent, "/api/method/login", {"usr": USER, "pwd": password})
	recorder.check(status == 200, f"login as {USER} through the front end", f"HTTP {status}")

	if status != 200:
		return 1

	# --- what JavaScript does the Desk actually hand the browser? -------------------------
	status, text = call(
		agent,
		"/api/method/frappe.desk.form.load.getdoctype",
		query={"doctype": "Logistics Settings"},
	)
	recorder.check(status == 200, "the form's meta is served", f"HTTP {status}")

	# `getdoctype` is Frappe's older response style: it writes its keys at the top level of the
	# response rather than under `message`, so the bundle is `docs`, not `message.docs`. Reading
	# the wrong level is the same mistake the browser made, and it is worth being explicit about
	# here: a probe that looks in the wrong place reports "no JavaScript" rather than "stale".
	try:
		payload = json.loads(text)
	except ValueError:
		payload = {}

	bundle = payload.get("docs") if isinstance(payload, dict) else None

	if bundle is None:
		bundle = ((payload or {}).get("message") or {}).get("docs")

	inline_js = ""

	for document in bundle or []:
		inline_js += document.get("__js") or ""

	print(f"       inline __js length: {len(inline_js)}")

	recorder.check(
		FIXED_JS_MARKER in inline_js,
		"the form's JavaScript is the corrected one (reads response.message)",
		"the meta still carries the old JavaScript — the browser will not send a token",
	)
	recorder.check(
		STALE_JS_MARKER not in inline_js,
		"the form's JavaScript no longer unwraps a response inside a discarded callback",
	)

	if not inline_js:
		print("       (no inline JavaScript arrived at all — the doctype_js hook is not resolving)")

	# --- the workflow, through the front end ---------------------------------------------
	status, text = call(agent, "/api/method/smartprocess_logistics.api.endpoints.export_serials")
	export = message_of(text)

	recorder.check(status == 200 and isinstance(export, dict), "export_serials answers", f"HTTP {status}")

	if not isinstance(export, dict):
		return 1

	content = json.dumps(export["document"], ensure_ascii=False, indent=1)

	print(f"       export counts: {export['counts']}")

	status, text = call(
		agent,
		"/api/method/smartprocess_logistics.api.endpoints.preview_serial_restore",
		{"content": content},
	)
	preview = message_of(text)

	recorder.check(
		status == 200 and isinstance(preview, dict), "preview_serial_restore answers", f"HTTP {status}"
	)

	if not isinstance(preview, dict):
		return 1

	token = preview.get("token")
	print(
		f"       preview: to_create={preview.get('to_create')} conflicts={preview.get('conflicts')} token={'yes' if token else 'MISSING'}"
	)

	recorder.check(bool(token), "the preview returns a token")

	# The request the browser was sending: content only.
	status, text = call(
		agent,
		"/api/method/smartprocess_logistics.api.endpoints.apply_serial_restore",
		{"content": content},
	)
	sentence = refusal_of(text)

	print(f"       apply without a token -> HTTP {status}: {sentence}")

	recorder.check(
		"missing 1 required positional argument" not in text,
		"a request without a token is refused in words, not by a TypeError from the dispatcher",
		text[:160],
	)
	recorder.check(
		"نشانهٔ تأیید پیش‌نمایش" in sentence,
		"the refusal names the missing token in Persian",
	)

	# The request the corrected browser sends.
	status, text = call(
		agent,
		"/api/method/smartprocess_logistics.api.endpoints.apply_serial_restore",
		{"content": content, "token": token},
	)
	applied = message_of(text)

	recorder.check(
		status == 200 and isinstance(applied, dict),
		"apply_serial_restore with the token answers",
		f"HTTP {status}",
	)

	if isinstance(applied, dict):
		print(f"       apply counts: {applied['counts']}")

		# A second identical request: the token is still valid, and the answer must be a no-op.
		status, text = call(
			agent,
			"/api/method/smartprocess_logistics.api.endpoints.apply_serial_restore",
			{"content": content, "token": token},
		)
		again = message_of(text)

		recorder.check(
			isinstance(again, dict) and again["counts"]["created"] == {"serials": 0, "assignments": 0},
			"a repeated submission creates nothing",
			str(again)[:160] if again else text[:160],
		)

	# A token that belongs to another file.
	status, text = call(
		agent,
		"/api/method/smartprocess_logistics.api.endpoints.apply_serial_restore",
		{"content": content, "token": "0" * 64},
	)

	recorder.check(
		"همان فایلی نیست" in refusal_of(text),
		"a token from another file is refused",
		refusal_of(text),
	)

	print(f"\n{'PASS' if not recorder.failures else f'FAIL ({recorder.failures} checks)'}")

	return 1 if recorder.failures else 0


if __name__ == "__main__":
	sys.exit(main())

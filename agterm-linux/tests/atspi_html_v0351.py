"""Exercise the v0.35 HTML bridge and browser store through real WebKit views."""

import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from atspi_smoke import activate, collect, focus_window, launch, raw_control_json, stop, wait_for, window_list, window_tree


def verify_html_v0351(env, state):
    pages = os.path.join(state, "bridge-pages")
    os.makedirs(pages, exist_ok=True)
    path = os.path.join(pages, "bridge.html")
    with open(path, "w", encoding="utf-8") as file:
        file.write('<!doctype html><title>Trusted DOM</title>'
                   '<script>document.title="unexpected page JS"</script>'
                   '<button type="button" data-agterm="session.rename" data-agterm-args=\'{"name":"DOM renamed"}\'>Rename from DOM</button>'
                   '<button type="button" data-agterm="session.overlay.submit" data-agterm-args=\'{"value":"chosen"}\'>Submit from DOM</button>')
    script_path = os.path.join(pages, "helper.html")
    with open(script_path, "w", encoding="utf-8") as file:
        file.write('<!doctype html><title>Helper pending</title><script>'
                   'agterm.request("session.rename",{args:{name:"JS renamed"}})'
                   '.then(()=>document.title="Helper ran")'
                   '.catch(e=>document.title="Helper error:"+e.message);</script>')
    storage_path = os.path.join(pages, "storage.html")
    with open(storage_path, "w", encoding="utf-8") as file:
        file.write('<!doctype html><title>Storage pending</title><script>'
                   'const value=new URLSearchParams(location.search).get("v")||"saved";'
                   'const cookie=(document.cookie.match(/(?:^|; )agterm-test=([^;]*)/)||[])[1]||"null";'
                   'document.title="storage:"+localStorage.getItem("agterm-test")+":"+cookie+":"+'
                   '(typeof agterm==="undefined"?"no-bridge":"unexpected-bridge");'
                   'localStorage.setItem("agterm-test",value);'
                   'document.cookie="agterm-test="+value+"; max-age=3600; path=/";</script>')

    stderr_log = os.environ.get("AGTERM_UI_APP_STDERR")
    assert stderr_log, "browser.clear probes require the smoke runner's captured app stderr"
    release = os.path.join(state, "browser-clear.release")
    assert not os.path.lexists(release), "browser.clear release marker must be fresh"
    process, app = launch(dict(env, AGTERM_ATSPI_BROWSER_CLEAR="hold,fail,cancel",
                               AGTERM_ATSPI_BROWSER_CLEAR_RELEASE=release))
    try:
        window = next(row["id"] for row in window_list(env) if row["open"])
        session = window_tree(env, window)["workspaces"][0]["sessions"][0]["id"]

        def request(cmd, args=None, target=session):
            reply = raw_control_json(env, {"cmd": cmd, "target": target, "args": dict(args or {}, window=window)})
            assert reply["ok"], reply
            return reply.get("result", {})

        def node():
            return next(row for workspace in window_tree(env, window)["workspaces"]
                        for row in workspace["sessions"] if row["id"] == session)

        def page():
            return next(iter(node().get("htmlOverlays", [])), None)

        def loaded(title):
            wait_for(lambda: page() and page().get("state") == "loaded" and page().get("title") == title,
                     f"page did not reach {title}: {page()}", timeout=20)

        def clear_events():
            # Ignore other scenario/instance PIDs and a record whose final newline is still being written.
            with open(stderr_log, encoding="utf-8", errors="replace") as source:
                prefix = f"AGTERM_BROWSER_CLEAR {process.pid} "
                return [line.split()[2:] for line in source if line.endswith("\n") and line.startswith(prefix)]

        opened = request("session.overlay.open", {"html": path, "cwd": pages, "chromeless": True})
        page_id = opened["pageID"]
        loaded("Trusted DOM")
        assert page()["chromeless"] is True, page()
        wait_for(lambda: collect(app, role="push button", name="Rename from DOM"), "DOM button missing")
        activate(collect(app, role="push button", name="Rename from DOM")[0])
        wait_for(lambda: node().get("name") == "DOM renamed", "DOM bridge did not route the default session target")
        zoom = page().get("zoom", 1)
        request("font.inc")
        assert page()["zoom"] > zoom
        request("font.reset")
        config = os.path.join(state, "config", "keymap.conf")
        with open(config, "a", encoding="utf-8") as file:
            file.write("\nmap ctrl+y increase_font_size\n")
        request("keymap.reload")
        focus_window(process.pid)
        subprocess.run(["xdotool", "key", "ctrl+y"], check=True)
        wait_for(lambda: page()["zoom"] > zoom, "rebound font shortcut changed the hidden terminal instead of the page")
        request("font.reset")
        activate(collect(app, role="push button", name="Submit from DOM")[0])
        wait_for(lambda: page() is None, "submitted page stayed open")
        outcome = raw_control_json(env, {"cmd": "session.overlay.result", "args": {"page": page_id}})
        assert outcome["ok"] and outcome["result"]["pageOutcome"]["value"] == "chosen", outcome

        request("session.overlay.open", {"html": script_path, "cwd": pages, "javascript": True})
        loaded("Helper ran")
        assert node().get("name") == "JS renamed", node()
        request("session.overlay.close")

        # Reload must re-read mutable sibling resources at the same granted URI, not just the document.
        reload_path, asset_path = [os.path.join(pages, name) for name in ("reload.html", "reload.js")]

        def write_reload_fixture(version):
            with open(reload_path, "w", encoding="utf-8") as file:
                file.write(f'<p id="version">Document {version}</p><script src="reload.js"></script>')
            with open(asset_path, "w", encoding="utf-8") as file:
                file.write(f'document.title=document.getElementById("version").textContent+":Asset {version}";')

        write_reload_fixture(1)
        request("session.overlay.open", {"html": reload_path, "cwd": pages, "javascript": True})
        loaded("Document 1:Asset 1")
        for version, current in ((2, False), (3, True)):
            write_reload_fixture(version)
            request("session.overlay.reload", {"current": current})
            loaded(f"Document {version}:Asset {version}")
        request("session.overlay.close")
        print("OK: original/current HTML reload refreshes the document and sibling script")

        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=pages))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = f"http://127.0.0.1:{server.server_port}/storage.html"
            def visit(persistent, expected, value=None):
                url = address + (f"?v={value}" if value else "")
                request("session.overlay.open", {"url": url, "persistent": persistent, "javascript": True})
                if expected is None:
                    wait_for(lambda: page() and page().get("state") == "loaded"
                             and page().get("title", "").startswith("storage:"),
                             "storage page did not load after real cancelled clear", timeout=20)
                    fields = page()["title"].split(":")
                    assert len(fields) == 4 and fields[3] == "no-bridge", fields
                    assert all(field in ("saved", "null") for field in fields[1:3]), fields
                    print(f"Storage after cancelled clear (removal may complete): {fields[1:3]}")
                else:
                    loaded(f"storage:{expected}:{expected}:no-bridge")
                if persistent:
                    refused = raw_control_json(env, {"cmd": "browser.clear"})
                    assert not refused["ok"] and "still open" in refused["error"], refused
                request("session.overlay.close")

            visit(True, "null")
            visit(True, "saved")
            visit(False, "null", value="ephemeral")
            visit(True, "saved")

            with ThreadPoolExecutor(max_workers=1) as pool:
                held = pool.submit(raw_control_json, env, {"cmd": "browser.clear"}, timeout=15)
                try:
                    wait_for(lambda: any(event[1:3] == ["hold", "start"] for event in clear_events()),
                             "the first clear never entered its held state", timeout=5)
                    busy = raw_control_json(env, {"cmd": "browser.clear"})
                    assert not busy["ok"] and busy["error"] == "browser.clear: browser storage is being cleared", busy
                    assert not any(event[1:3] == ["hold", "finish"] for event in clear_events()), \
                        "the held clear completed before the busy assertion"
                finally:
                    with open(release, "w", encoding="utf-8") as marker:
                        marker.write("release\n")
                cleared = held.result(timeout=15)
            assert cleared["ok"], cleared
            visit(True, "null")

            # Retain PR215's synthetic failure/data-preservation assertion independently of cancellation.
            failed = raw_control_json(env, {"cmd": "browser.clear"})
            assert not failed["ok"] and "injected by AGTERM_ATSPI_BROWSER_CLEAR" in failed["error"], failed
            visit(True, "saved")

            cancelled = raw_control_json(env, {"cmd": "browser.clear"})
            assert not cancelled["ok"] and cancelled["error"].startswith("browser.clear: "), cancelled
            assert "injected by" not in cancelled["error"], cancelled
            # WebKit removes data before returning its GTask result. Cancellation need not roll removal back.
            # A successful open proves the clearing flag reset; seed fresh data before the normal retry.
            visit(True, None, value="after-cancel")
            visit(True, "after-cancel")
            retried = raw_control_json(env, {"cmd": "browser.clear"})
            assert retried["ok"], retried
            request("session.overlay.open", {"url": address, "persistent": True, "javascript": True})
            loaded("storage:null:null:no-bridge")
            request("session.overlay.close")
            assert os.path.isdir(os.path.join(state, "browser")), "browser storage escaped the isolated state directory"
            other_state = os.path.join(state, "other-instance")
            os.makedirs(other_state)
            other_env = dict(env, AGTERM_STATE_DIR=other_state,
                             AGTERM_CONTROL_SOCKET=os.path.join(other_state, "agterm.sock"),
                             AGTERM_APP_ID=env["AGTERM_APP_ID"] + ".browser_other")
            other_process, _other_app = launch(other_env)
            try:
                other_window = next(row["id"] for row in window_list(other_env) if row["open"])
                other_session = window_tree(other_env, other_window)["workspaces"][0]["sessions"][0]["id"]
                opened = raw_control_json(other_env, {"cmd": "session.overlay.open", "target": other_session,
                                                     "args": {"url": address, "persistent": True, "javascript": True}})
                assert opened["ok"], opened

                def other_page():
                    row = window_tree(other_env, other_window)["workspaces"][0]["sessions"][0]
                    return next(iter(row.get("htmlOverlays", [])), {})

                wait_for(lambda: other_page().get("title") == "storage:null:null:no-bridge",
                         "separate agterm instance reused the first instance's browser data", timeout=20)
            finally:
                stop(other_process)

            background = request("session.new", {"name": "background browser"})["id"]
            request("session.select")
            request("session.overlay.open", {"url": address, "persistent": True}, target=background)
            refused = raw_control_json(env, {"cmd": "browser.clear"})
            assert not refused["ok"] and "still open" in refused["error"], refused
            request("session.overlay.close", target=background)
        finally:
            server.shutdown()
            server.server_close()

        contexts = {}
        for identity, mode, event, *detail in clear_events():
            entry = contexts.setdefault(identity, {"mode": mode, "start": [], "callback": [], "finish": []})
            assert entry["mode"] == mode and event in ("start", "callback", "finish"), (identity, mode, event)
            entry[event].append(detail)
        assert sorted(entry["mode"] for entry in contexts.values()) == ["cancel", "clear", "fail", "hold"], contexts
        for entry in contexts.values():
            assert entry["start"] == [[]] and entry["finish"] == [["1"]], entry
            expected_callback = [] if entry["mode"] == "fail" else [["1", "cancelled" if entry["mode"] == "cancel" else "ok"]]
            assert entry["callback"] == expected_callback, entry
        print("OK: trusted DOM and JS bridges, page submit/outcome, zoom, persistent/ephemeral cookie and storage "
              "isolation; browser.clear held exclusion, real cancellation/retry and exactly-once callbacks/completions")
    finally:
        stop(process)

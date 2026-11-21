"""Real WebKit page and control-socket coverage under the isolated GTK smoke runner."""

import os
import json
import subprocess
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from atspi_smoke import CTL, collect, control_json, launch, raw_control_json, stop, wait_for, window_list, window_tree


def title_class(title):
    if not title:
        return "none"
    return "initial" if title == "Paste check" else "pasted" if title.startswith("pasted:") else "other"


class PasteTrace:
    """Correlates producer steps with the app's paste trace lines (#421): types, booleans and CLOCK_MONOTONIC only."""

    PREFIX = "agterm-paste-trace: "

    def __init__(self):
        self.log = os.environ.get("AGTERM_UI_APP_STDERR")

    def mark(self):
        return os.path.getsize(self.log) if self.log and os.path.exists(self.log) else None

    def report(self, phase, mark, producer):
        origin = producer["set"]
        steps = " ".join(f"{name}={(value - origin) * 1000:.1f}ms" if isinstance(value, float) else f"{name}={value}"
                         for name, value in producer.items())
        print(f"html-paste-trace {phase}: producer {steps}")
        if mark is None:
            print(f"html-paste-trace {phase}: app trace unavailable (AGTERM_UI_APP_STDERR unset)")
            return
        with open(self.log, encoding="utf-8", errors="replace") as source:
            source.seek(mark)
            lines = [line[len(self.PREFIX):] for line in source.read().splitlines() if line.startswith(self.PREFIX)]
        for line in lines:
            fields = line.split(" ")
            at = next((field for field in fields if field.startswith("at=")), None)
            relative = f"{int(at[3:]) / 1000 - origin * 1000:.1f}ms" if at and at[3:].isdigit() else "?"
            print(f"html-paste-trace {phase}: app +{relative} " + " ".join(field for field in fields if field != at))
        if not lines:
            print(f"html-paste-trace {phase}: no app trace lines")


def verify_html_overlay(env, state):
    def cli(*args):
        command = subprocess.run([CTL, *args, "--socket", env["AGTERM_CONTROL_SOCKET"]],
                                 env=env, capture_output=True, text=True, timeout=10)
        assert command.returncode == 0, f"agtermctl failed: {command.stdout} {command.stderr}"
        return json.loads(command.stdout)

    pages = os.path.join(state, "pages")
    os.makedirs(pages)
    path = os.path.join(pages, "index.html")
    with open(path, "w", encoding="utf-8") as target:
        target.write("<!doctype html><title>Linux page overlay</title><h1>Rendered by WebKit</h1>")
    scripted = os.path.join(pages, "scripted.html")
    with open(scripted, "w", encoding="utf-8") as target:
        target.write('<!doctype html><title>Pending script</title><script src="inside.js"></script>'
                     '<script src="../outside.js"></script><script src="link.js"></script>')
    with open(os.path.join(pages, "inside.js"), "w", encoding="utf-8") as target:
        target.write('document.title = "Granted script loaded";')
    with open(os.path.join(state, "outside.js"), "w", encoding="utf-8") as target:
        target.write('document.title = "Unapproved script loaded";')
    os.symlink(os.path.join(state, "outside.js"), os.path.join(pages, "link.js"))

    # PASTE_TRACE is DEBUG-only and observation-only (LinuxHtmlPasteTrace.swift); see paste_trace below.
    process, app = launch(dict(env, AGTERM_ATSPI_PASTE_TRACE="1"))
    try:
        window = next(item["id"] for item in window_list(env) if item["open"])
        session = window_tree(env, window)["workspaces"][0]["sessions"][0]["id"]

        def page(pane=None):
            node = next(item for workspace in window_tree(env, window)["workspaces"]
                        for item in workspace["sessions"] if item["id"] == session)
            return next((item for item in node.get("htmlOverlays", []) if item.get("pane") == pane), None)

        opened = cli("session", "overlay", "open", "--html", path,
                              "--cwd", pages, "--navigation", "--target", session,
                              "--window", window, "--json")
        assert opened["ok"], opened
        wait_for(lambda: page() and page()["state"] == "loaded" and
                 page().get("title") == "Linux page overlay",
                 f"WebKit did not load the local page: {page()}", timeout=20)
        assert page()["page"] == path, page()
        assert control_json(env, "session", "overlay", "reload", "--target", session,
                            "--window", window, "--json")["ok"]
        wait_for(lambda: page() and page()["state"] == "loaded", "WebKit did not reload the page")

        for command, error in (("session.overlay.result", "no overlay result: the slot holds an html page"),
                               ("session.overlay.text", "no overlay to read: the slot holds an html page")):
            reply = raw_control_json(env, {"cmd": command, "target": session, "args": {"window": window}})
            assert not reply["ok"] and reply.get("error") == error, reply

        assert control_json(env, "session", "overlay", "close", "--target", session,
                            "--window", window, "--json")["ok"]
        wait_for(lambda: page() is None, "closed page remained in the control tree")

        opened = cli("session", "overlay", "open", "--html", path,
                              "--size-percent", "60", "--target", session,
                              "--window", window, "--json")
        assert opened["ok"], opened
        wait_for(lambda: page() and page()["state"] == "loaded", "floating page did not load")
        assert control_json(env, "session", "overlay", "close", "--target", session,
                            "--window", window, "--json")["ok"]

        assert control_json(env, "session", "split", "on", "--target", session,
                            "--window", window, "--json")["ok"]
        opened = cli("session", "overlay", "open", "--html", path,
                              "--cwd", pages, "--pane", "right", "--target", session,
                              "--window", window, "--json")
        assert opened["ok"], opened
        wait_for(lambda: page("right") and page("right")["state"] == "loaded",
                 f"pane page did not load: {page('right')}")
        wait_for(lambda: collect(app, role="document web"), "pane page was not visible")
        assert control_json(env, "surface", "zoom", "show",
                            "--target", f"surface:{session}:split", "--window", window, "--json")["ok"]
        wait_for(lambda: not collect(app, role="document web"),
                 "pane HTML page still covered the zoomed split terminal")
        assert control_json(env, "surface", "zoom", "hide",
                            "--target", f"surface:{session}:split", "--window", window, "--json")["ok"]
        wait_for(lambda: collect(app, role="document web"), "pane page did not return after zoom exit")
        assert control_json(env, "session", "overlay", "close", "--pane", "right",
                            "--target", session, "--window", window, "--json")["ok"]
        wait_for(lambda: page("right") is None, "closed pane page remained in the control tree")

        opened = cli("session", "overlay", "open", "--html", scripted,
                     "--cwd", pages, "--target", session, "--window", window, "--json")
        assert opened["ok"], opened
        wait_for(lambda: page() and page()["state"] == "loaded" and
                 page().get("title") == "Pending script",
                 f"JavaScript ran without --js: {page()}")
        assert control_json(env, "session", "overlay", "close", "--target", session,
                            "--window", window, "--json")["ok"]

        opened = cli("session", "overlay", "open", "--html", scripted,
                     "--js", "--target", session, "--window", window, "--json")
        assert opened["ok"], opened
        wait_for(lambda: page() and page()["state"] == "loaded" and
                 page().get("title") == "Pending script",
                 f"a file page without --cwd loaded a local asset: {page()}")
        assert control_json(env, "session", "overlay", "close", "--target", session,
                            "--window", window, "--json")["ok"]

        opened = cli("session", "overlay", "open", "--html", scripted,
                     "--cwd", pages, "--js", "--target", session,
                     "--window", window, "--json")
        assert opened["ok"], opened
        wait_for(lambda: page() and page()["state"] == "loaded" and
                 page().get("title") == "Granted script loaded",
                 f"WebKit did not load the granted script or loaded an outside file: {page()}")
        assert control_json(env, "session", "overlay", "close", "--target", session,
                            "--window", window, "--json")["ok"]

        class QuietHandler(SimpleHTTPRequestHandler):
            redirect_to = None

            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path == "/redirect" and self.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", self.redirect_to)
                    self.end_headers()
                else:
                    super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=pages))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        other = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=pages))
        other_thread = threading.Thread(target=other.serve_forever, daemon=True)
        other_thread.start()
        QuietHandler.redirect_to = f"http://127.0.0.1:{other.server_port}/index.html"
        try:
            address = f"http://127.0.0.1:{server.server_port}/index.html"
            opened = cli("session", "overlay", "open", "--url", address,
                         "--target", session, "--window", window, "--json")
            assert opened["ok"], opened
            wait_for(lambda: page() and page()["state"] == "loaded" and
                     page().get("title") == "Linux page overlay",
                     f"WebKit did not load the local HTTP page: {page()}")
            assert page()["page"] == address, page()
            assert control_json(env, "session", "overlay", "close", "--target", session,
                                "--window", window, "--json")["ok"]
            opened = cli("session", "overlay", "open", "--url",
                         f"http://127.0.0.1:{server.server_port}/redirect",
                         "--target", session, "--window", window, "--json")
            assert opened["ok"], opened
            wait_for(lambda: page() and page()["state"] == "failed",
                     f"a redirect to another origin was not blocked: {page()}")
            assert "navigation blocked" in page().get("error", ""), page()
            assert control_json(env, "session", "overlay", "close", "--target", session,
                                "--window", window, "--json")["ok"]
        finally:
            server.shutdown()
            server.server_close()
            other.shutdown()
            other.server_close()

        paste_path = os.path.join(pages, "paste.html")
        with open(paste_path, "w", encoding="utf-8") as target:
            target.write('<!doctype html><title>Paste check</title>'
                         '<textarea style="width:100vw;height:100vh" '
                         'oninput="document.title=\'pasted:\'+this.value"></textarea>')
        opened = cli("session", "overlay", "open", "--html", paste_path, "--js",
                     "--target", session, "--window", window, "--json")
        assert opened["ok"], opened
        wait_for(lambda: page() and page()["state"] == "loaded" and page().get("title") == "Paste check",
                 "paste test page did not load")

        Gtk.init()
        clipboard = Gdk.Display.get_default().get_clipboard()

        def send_into_page(*keys):
            from atspi_smoke import collect, find_app, mouse_click

            mouse_click(lambda: next(iter(collect(find_app(process.pid), role="document web")), None),
                        process.pid, button="left")
            subprocess.run(["xdotool", *keys], check=True)

        def wait_clipboard(predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                if predicate():
                    return
                time.sleep(0.05)
            assert predicate(), f"clipboard test did not reach expected page state: {page()}"

        paste_trace = PasteTrace()
        plain = GObject.Value(GObject.TYPE_STRING)
        plain.set_string("plain")
        plain_mark = paste_trace.mark()
        plain_set = time.monotonic()
        clipboard.set(plain)
        plain_send = time.monotonic()
        # The clipboard owner only answers TARGETS once wait_clipboard pumps GLib.
        # Press a modifier in the meantime to exercise normal keyboard activity during the wait.
        send_into_page("key", "--clearmodifiers", "ctrl+v", "key", "Shift_L")
        plain_sent = time.monotonic()
        try:
            wait_clipboard(lambda: page() and page().get("title") == "pasted:plain")
        finally:
            plain_result = page() or {}
            paste_trace.report("plain", plain_mark, {"set": plain_set, "sendStart": plain_send, "sendEnd": plain_sent,
                                                     "observed": time.monotonic(),
                                                     "title": title_class(plain_result.get("title"))})

        assert cli("session", "overlay", "reload", "--target", session,
                   "--window", window, "--json")["ok"]
        wait_for(lambda: page() and page().get("title") == "Paste check", "paste page did not reload")
        uri_provider = Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new(b"file:///tmp/x\r\n"))
        blocked = GObject.Value(GObject.TYPE_STRING)
        blocked.set_string("blocked")
        text_provider = Gdk.ContentProvider.new_for_value(blocked)
        mixed_mark = paste_trace.mark()
        mixed_set = time.monotonic()
        clipboard.set_content(Gdk.ContentProvider.new_union([uri_provider, text_provider]))
        producer_uri = clipboard.get_formats().contain_mime_type("text/uri-list")
        assert producer_uri
        time.sleep(1)
        mixed_send = time.monotonic()
        send_into_page("key", "--clearmodifiers", "ctrl+v")
        mixed_sent = time.monotonic()
        for _ in range(30):
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(0.05)
        final = page()
        paste_trace.report("mixed", mixed_mark, {"set": mixed_set, "producerUriList": producer_uri,
                                                 "sendStart": mixed_send, "sendEnd": mixed_sent,
                                                 "observed": time.monotonic(), "title": title_class(final.get("title"))})
        assert final.get("title") == "Paste check", f"URI-list clipboard reached the page: {page()}"
        assert cli("session", "overlay", "close", "--target", session,
                   "--window", window, "--json")["ok"]

        print("OK: WebKit loaded full, floating, pane, and HTTP pages; JavaScript, file grants, and URI-list paste stayed scoped")
    finally:
        stop(process)

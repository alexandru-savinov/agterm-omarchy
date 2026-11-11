"""HTML bridge isolation and WebKit navigation verdicts compared with HtmlNavigationPolicy."""

import json
import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from atspi_smoke import activate, collect, find_app, launch, mouse_click, named, poll, raw_control_json, stop, wait_for, window_list, window_tree

SETTLE = 2.5


def verify_html_policy(env, state):
    pages = os.path.join(state, "policy-pages")
    os.makedirs(pages)
    hits = []

    class Recorder(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            hits.append(self.path)
            super().do_GET()

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), partial(Recorder, directory=pages)) for _ in range(2)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    home, other = (f"http://127.0.0.1:{server.server_port}" for server in servers)

    def write(name, body):
        path = os.path.join(pages, name)
        with open(path, "w", encoding="utf-8") as target:
            target.write(body)
        return path

    write("other.html", "<!doctype html><title>Policy other</title><p>other</p>")
    # Subframes report through window.postMessage: file pages do not share an origin by path, so a subframe
    # cannot be assumed to reach parent.document; the parent page turns its own frame's message into its title.
    write("framed.html", '<!doctype html><title>framed</title><script>parent.postMessage("framed","*")</script>')
    relay = ('<script>addEventListener("message",event=>{if(event.source===frames[0])'
             'document.title=String(event.data)})</script>')

    process, app = launch(env)
    try:
        window = next(row["id"] for row in window_list(env) if row["open"])
        session = window_tree(env, window)["workspaces"][0]["sessions"][0]["id"]

        def request(cmd, args=None):
            reply = raw_control_json(env, {"cmd": cmd, "target": session, "args": dict(args or {}, window=window)})
            assert reply["ok"], reply
            return reply.get("result", {})

        def node():
            return next(row for workspace in window_tree(env, window)["workspaces"]
                        for row in workspace["sessions"] if row["id"] == session)

        def page():
            return next(iter(node().get("htmlOverlays", [])), None)

        def open_page(args, title):
            # A page may retitle itself (bridge reply, subframe message) before a poll sees its first title,
            # so a finished load counts too; the caller then checks the title it is waiting for.
            request("session.overlay.open", args)
            if not wait_for(lambda: page() and (page().get("title") == title or page().get("state") == "loaded"),
                            "", timeout=20, required=False):
                raise AssertionError(f"page did not reach {title!r}: {page()}")

        def close_page():
            request("session.overlay.close")
            wait_for(lambda: page() is None, "closed page remained in the control tree")

        def titled(prefix):
            found = wait_for(lambda: page() and (page().get("title") or "").startswith(prefix) and page()["title"],
                             "", timeout=20, required=False)
            if not found:
                raise AssertionError(f"page never reported {prefix!r}: {page()}")
            return found

        base = "policy base"
        request("session.rename", {"name": base})

        settle = ('const settle=(key,promise)=>promise.then(()=>results[key]="dispatched",'
                  'error=>results[key]="rejected:"+error.message);')
        refused = "rejected:request must come from the file page's main frame"
        write("wrong.html", '<!doctype html><title>Wrong pending</title><script>const results={};' + settle +
              'const native=window.webkit.messageHandlers.agterm;'
              'addEventListener("load",()=>setTimeout(()=>Promise.all([settle("wrong-nonce",native.postMessage({nonce:"guess",'
              'request:{cmd:"session.rename",args:{name:"wrong nonce"}}})),'
              'settle("no-envelope",native.postMessage({cmd:"session.rename",args:{name:"no envelope"}}))])'
              '.then(()=>document.title="wrong:"+JSON.stringify(results)),300))</script>')
        open_page({"html": os.path.join(pages, "wrong.html"), "cwd": pages, "javascript": True}, "Wrong pending")
        outcome = json.loads(titled("wrong:")[len("wrong:"):])
        assert outcome == {"wrong-nonce": refused, "no-envelope": refused}, outcome
        close_page()

        write("child.html", '<!doctype html><title>child</title><script>const results={};' + settle +
              'const own=window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers.agterm;'
              'const tries=[];addEventListener("load",()=>setTimeout(()=>{'
              'if(own){tries.push(settle("frame-handler",own.postMessage({nonce:"guess",'
              'request:{cmd:"session.rename",args:{name:"frame handler"}}})))}else{results["frame-handler"]="absent"}'
              'try{tries.push(settle("parent-handler",parent.webkit.messageHandlers.agterm.postMessage('
              '{cmd:"session.rename",args:{name:"parent handler"}})))}catch(e){results["parent-handler"]="threw"}'
              'try{tries.push(settle("parent-helper",parent.agterm.request("session.rename",'
              '{args:{name:"parent helper"}})))}catch(e){results["parent-helper"]="threw"}'
              'try{results["parent-document"]=typeof parent.document.title}catch(e){results["parent-document"]="threw"}'
              'Promise.all(tries).then(()=>parent.postMessage("frame:"+JSON.stringify(results),"*"))},300))</script>')
        write("frames.html",
              '<!doctype html><title>Frames pending</title>' + relay + '<iframe src="child.html"></iframe>')
        open_page({"html": os.path.join(pages, "frames.html"), "cwd": pages, "javascript": True}, "Frames pending")
        outcome = json.loads(titled("frame:")[len("frame:"):])
        print(f"HTML subframe bridge outcome: {outcome}")
        time.sleep(0.5)
        frame_name = node().get("name")
        close_page()
        assert outcome.get("frame-handler") in ("absent", refused), outcome
        assert outcome.get("parent-handler") in ("threw", refused), outcome
        assert outcome.get("parent-helper") != "dispatched" and frame_name == base, \
            f"a same-origin subframe dispatched through the main frame's helper: {outcome}, name {frame_name!r}"

        write("valid.html", '<!doctype html><title>Valid pending</title><script>'
              'agterm.request("session.rename",{args:{name:"main frame"}})'
              '.then(()=>document.title="valid:ok",e=>document.title="valid:"+e.message)</script>')
        open_page({"html": os.path.join(pages, "valid.html"), "cwd": pages, "javascript": True}, "Valid pending")
        assert titled("valid:") == "valid:ok", page()
        wait_for(lambda: node().get("name") == "main frame", "the main-frame bridge request did not rename")
        close_page()
        request("session.rename", {"name": base})

        write("ticker.html", '<!doctype html><title>Ticker</title><script>let tick=0;'
              'setInterval(()=>agterm.request("session.rename",{args:{name:"tick "+(tick++)}}),100)</script>')
        open_page({"html": os.path.join(pages, "ticker.html"), "cwd": pages, "javascript": True}, "Ticker")
        first = wait_for(lambda: (node().get("name") or "").startswith("tick ") and node()["name"],
                         "ticker page never reached the bridge")
        wait_for(lambda: node().get("name") != first, "ticker page stopped before close")
        close_page()
        after_close = node().get("name")
        time.sleep(1.5)
        assert node().get("name") == after_close, f"a closed page still renamed the session: {node().get('name')}"
        request("session.rename", {"name": base})

        # Expected verdicts are HtmlNavigationPolicy.decide for (source, target, userActivated, url).
        file_cases = [
            ("file", "same-origin script nav", 'location="other.html"', None, "allow:Policy other"),
            ("file", "cross-origin script redirect", f'location="{other}/other.html?case=f-redirect"', None, "cancel"),
            ("file", "user-clicked http link", f'<a href="{other}/other.html?case=f-link">go</a>', "click",
             "openExternal"),
            ("file", "same-grant subframe", relay + '<iframe src="framed.html"></iframe>', "html", "allow:framed"),
            ("file", "http subframe", f'<iframe src="{other}/other.html?case=f-frame"></iframe>', "html", "cancel"),
            ("file", "script window.open", f'window.open("{other}/other.html?case=f-open")', None, "cancel"),
            ("file", "target=_blank click", f'<a target="_blank" href="{other}/other.html?case=f-blank">go</a>',
             "click", "openExternal"),
        ]
        url_cases = []
        for mode in ("url", "browse"):
            tag = mode[0]
            url_cases += [
                (mode, "same-origin script nav", f'location="{home}/other.html?case={tag}-same"', None,
                 f"allow:{tag}-same"),
                (mode, "cross-origin script redirect", f'location="{other}/other.html?case={tag}-redirect"', None,
                 "cancel"),
                (mode, "cross-origin subframe", f'<iframe src="{other}/other.html?case={tag}-frame"></iframe>', "html",
                 f"allow:{tag}-frame"),
                (mode, "script window.open", f'window.open("{other}/other.html?case={tag}-open")', None, "cancel"),
            ]
        url_cases += [
            ("url", "user-clicked cross-origin link", f'<a href="{other}/other.html?case=u-link">go</a>', "click",
             "openExternal"),
            ("url", "target=_blank click", f'<a target="_blank" href="{other}/other.html?case=u-blank">go</a>',
             "click", "openExternal"),
        ]

        def dialog():
            return next((item for item in collect(find_app(process.pid))
                         if (item.get_name() or "").endswith(" in your browser?")), None)

        rows = []
        for index, (mode, label, body, kind, expected) in enumerate(file_cases + url_cases):
            if kind is None:
                markup = f"<script>setTimeout(()=>{{{body}}},300)</script>"
            elif kind == "click":
                markup = body.replace("<a ", '<a style="display:block;font-size:64px" ')
            else:
                markup = body
            name = write(f"case{index}.html", f"<!doctype html><title>Case {index}</title>{markup}")
            if mode == "file":
                args = {"html": name, "cwd": pages, "javascript": True}
            else:
                args = {"url": f"{home}/case{index}.html", "javascript": True, "navigation": mode == "browse"}
            open_page(args, f"Case {index}")
            if kind == "click":
                mouse_click(lambda: named(find_app(process.pid), "go", role="link"), process.pid, button="left")
            title = expected.split(":", 1)[1] if expected.startswith("allow:") and "case=" not in body else None
            case = body.split("case=", 1)[1].split('"', 1)[0] if "case=" in body else None

            def allowed():
                if title:
                    return page() and page().get("title") == title
                return any(f"case={case}" in hit for hit in hits)

            if expected == "openExternal":
                poll(dialog, SETTLE + 3)
            elif expected.startswith("allow"):
                poll(allowed, SETTLE + 3)
            else:
                time.sleep(SETTLE)
            prompt = dialog()
            observed = "openExternal" if prompt else "allow" if allowed() else "cancel"
            if prompt:
                activate(named(find_app(process.pid), "Cancel", role="button"))
                wait_for(lambda: dialog() is None, "external-open prompt did not dismiss")
            state_now = page() or {}
            rows.append((mode, label, expected.split(":", 1)[0], observed, state_now.get("state"),
                         state_now.get("error") or ""))
            close_page()

        table = "\n".join(f"  {mode:6} {label:32} policy={want:12} webkit={got:12} state={st} {err}"
                          for mode, label, want, got, st, err in rows)
        print("HTML navigation verdicts:\n" + table)
        mismatched = [row for row in rows if row[2] != row[3]]
        assert not mismatched, "WebKit adapter verdicts diverged from HtmlNavigationPolicy:\n" + table
        for mode, label, _want, _got, st, err in rows:
            if "script redirect" in label:
                assert st == "failed" and "navigation blocked" in err, (mode, label, st, err)
            elif "subframe" in label:
                assert st == "loaded", f"a subframe verdict failed the whole page: {mode} {label} {st} {err}"
        print("OK: HTML bridge refused wrong nonces, subframes, and closed pages; WebKit navigation verdicts matched "
              "HtmlNavigationPolicy in file, url, and browse modes")
    finally:
        stop(process)
        for server in servers:
            server.shutdown()
            server.server_close()

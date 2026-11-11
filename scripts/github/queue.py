#!/usr/bin/env python3
"""Publish a read-only snapshot of GitHub task assignments and agent check-ins."""
import argparse
import datetime
import html
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path


def checkin(body):
    """Parse data fields without treating a check-in as an ownership grant."""
    lines = body.replace("\r\n", "\n").split("\n")
    header_index = next((i for i, line in enumerate(lines) if line.strip(" \t")), None)
    if header_index is None:
        return None
    match = re.fullmatch(r"Agent: *(?:\*\*([a-z0-9_-]+)\*\*|([a-z0-9_-]+))",
                         lines[header_index].rstrip(" "), re.IGNORECASE | re.ASCII)
    if not match:
        return None
    labels = {"task": "Task", "owned paths": "Owned paths", "state": "State", "team": "Team", "session": "Session"}
    fields = {key: [] for key in labels}
    sequence, uncertainties = [], []
    # Parse a small data format, not Markdown. Only the immediate, canonical
    # field block can advance a claim; keep rejected raw fields for diagnosis.
    # re.ASCII: case-insensitive matching must not fold non-ASCII letters onto the labels.
    for line in lines[header_index + 1:]:
        field = re.fullmatch(r"(Task|Owned paths|State|Team|Session): ?(.*)", line, re.IGNORECASE | re.ASCII)
        if not field:
            break
        key, value = field[1].lower(), field[2]
        fields[key].append(value)
        sequence.append(key)
        if line != f"{labels[key]}: {value}":
            uncertainties.append("field block: use exact labels followed by one space")
    required = ["task", "owned paths", "state"]
    allowed = (required, required + ["team"], required + ["session"], required + ["team", "session"])
    if sequence not in allowed:
        uncertainties.append("field block: expected exactly Task, Owned paths, State in that order, "
                             "optionally followed by Team, then Session")
    values = {}
    for key, entries in fields.items():
        if key in ("team", "session") and not entries:
            values[key] = None
        elif len(entries) != 1 or not entries[0]:
            uncertainties.append(f"{key}: {'duplicate' if len(entries) > 1 else 'missing or empty'} field")
            values[key] = None
        else:
            values[key] = entries[0]
    task = values["task"]
    if task is not None and not re.fullmatch(r"#[1-9][0-9]{0,9}", task):
        uncertainties.append("task: expected exactly one #issue-number")
        task = None
    paths = values["owned paths"]
    # Intraword underscores are literal in Markdown; leading/trailing ones can
    # start emphasis that spans fields. Keep the accepted path alphabet explicit.
    path = r"[A-Za-z0-9.](?:[A-Za-z0-9./-]|(?<=[A-Za-z0-9])_(?=[A-Za-z0-9]))*"
    if paths is not None and not re.fullmatch(rf"{path}(?:, {path})*", paths):
        uncertainties.append("owned paths: expected literal ASCII paths separated by comma and space")
    state = values["state"]
    if state is not None and state not in ("working", "blocked", "ready-for-review", "complete"):
        uncertainties.append("state: unrecognized value")
        state = None
    team = values["team"]
    if team is not None and not re.fullmatch(r"[1-9][0-9]{0,3}", team):
        uncertainties.append("team: expected a team size from 1 to 9999")
    session = values["session"]
    if session is not None and not re.fullmatch(r"[a-z0-9]{4,16}", session):
        uncertainties.append("session: expected 4-16 lowercase letters or digits")
    if uncertainties:
        # Even a syntactically valid Task cannot let an ambiguous later comment
        # replace an earlier observation. Raw fields remain in the report.
        task = state = None
    return {"name": (match[1] or match[2]).lower(), "task": int(task[1:]) if task else None,
            "owned_paths": values["owned paths"], "state": state, "session": session,
            "team": int(team) if team and re.fullmatch(r"[1-9][0-9]{0,3}", team) else None,
            "field_values": fields, "uncertainties": uncertainties}


STALE_AFTER_SECONDS = 4 * 3600
# Only accounts with write-level standing on the repository can claim ownership or report headcount.
# Anyone else's check-in stays visible as unverified, and never changes a task's coordination.
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
UNVERIFIED = "unverified (not a collaborator)"
UNPARSEABLE = "comment could not be parsed; read the source comment"


def parse_time(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def age_text(seconds):
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}h{rest // 60:02d}m"


def observation_order(record):
    # Source URL supplies deterministic ordering when timestamps tie.
    return record["updated_at"], record["url"]


def observe(comment, number):
    """One comment as a record, or None for ordinary discussion. May raise on malformed input."""
    parsed = checkin(comment.get("body") or "")
    if parsed is None:
        return None
    association = str(comment.get("author_association") or "NONE")
    return {**parsed, "account": comment["user"]["login"], "issue": number, "url": comment["html_url"],
            "updated_at": comment["updated_at"],
            # Age counts from creation: editing an old check-in must not reset it.
            "created_at": comment.get("created_at", comment["updated_at"]),
            "author_association": association, "verified": association in TRUSTED_ASSOCIATIONS}


def unparseable(comment, number):
    """An uncertain record for a comment the parser could not read; the snapshot never fails on content."""
    def text(value):
        return value if isinstance(value, str) else ""
    comment = comment if isinstance(comment, dict) else {}
    user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    association = text(comment.get("author_association")) or "NONE"
    return {"name": "unparseable", "task": None, "owned_paths": None, "state": None, "session": None,
            "team": None, "field_values": {key: [] for key in ("task", "owned paths", "state", "team", "session")},
            "uncertainties": [UNPARSEABLE], "account": text(user.get("login")) or "unknown", "issue": number,
            "url": text(comment.get("html_url")), "updated_at": text(comment.get("updated_at")),
            "created_at": text(comment.get("created_at")) or text(comment.get("updated_at")),
            "author_association": association, "verified": association in TRUSTED_ASSOCIATIONS}


def set_age(latest, now):
    if now is None:
        latest["age_seconds"], latest["stale"] = None, False
    else:
        latest["age_seconds"] = max(0, int((now - parse_time(latest["created_at"])).total_seconds()))
        # Only a quiet `working` claim is stale; blocked/ready-for-review wait on others.
        latest["stale"] = latest["state"] == "working" and latest["age_seconds"] > STALE_AFTER_SECONDS


def snapshot(issues, comments, now=None, since=None):
    """Build the snapshot. `now` is injected so ages are reproducible from observed_at.

    `since` is the start of the comment window, if one was applied when fetching."""
    tasks = []
    agents = {}
    uncertain = []
    for issue in issues:
        if "pull_request" in issue:
            continue
        tasks.append({key: issue[key] for key in ("number", "title", "state", "html_url")})
        tasks[-1]["labels"] = [item["name"] for item in issue.get("labels", [])]
        tasks[-1]["assignees"] = [item["login"] for item in issue.get("assignees", [])]
        for comment in comments.get(issue["number"], []):
            try:
                record = observe(comment, issue["number"])
                if record is None:
                    continue
                identity = (record["account"].lower(), record["name"], record["verified"])
            except Exception:  # noqa: BLE001 - one malformed comment must not fail the whole snapshot
                uncertain.append(unparseable(comment, issue["number"]))
                continue
            agents.setdefault(identity, []).append(record)
    task_index = {task["number"]: task for task in tasks}
    claims, unverified_claims = [], []
    latest_agents = []
    for (account, name, verified), records in sorted(agents.items()):
        records.sort(key=observation_order)
        by_task = {}
        for record in records:
            if record["task"] is None:
                uncertain.append(record)
                continue
            if record["task"] not in task_index:
                record["uncertainties"].append("task: issue absent from this task snapshot")
                uncertain.append(record)
                continue
            by_task.setdefault(record["task"], []).append(record)
        if verified:
            latest_agents.append(records[-1])
        for number, history in sorted(by_task.items()):
            # Unverified claims keep their task and fields for display, but never enter `claims`.
            (claims if verified else unverified_claims).append(
                {"account": account, "name": name, "task": number, "latest": history[-1], "history": history})
    for claim in claims + unverified_claims:
        set_age(claim["latest"], now)
    # One name, several live sessions: two runs share a name and the queue cannot tell them apart.
    sessions_by_identity = {}
    for claim in claims:
        latest = claim["latest"]
        sessions = sessions_by_identity.setdefault((claim["account"].lower(), claim["name"]), set())
        if latest["state"] not in (None, "complete") and not latest["stale"] and latest["session"]:
            sessions.add(latest["session"])
        # On one task a second session supersedes `latest`, so an earlier session counts while its own
        # newest check-in there is non-complete and under the stale age; a restart ages out by itself.
        newest_by_session = {record["session"]: record for record in claim["history"] if record["session"]}
        for session, record in newest_by_session.items():
            if (record is not latest and now is not None and record["state"] not in (None, "complete")
                    and (now - parse_time(record["created_at"])).total_seconds() <= STALE_AFTER_SECONDS):
                sessions.add(session)
    collisions = [(account, name, sorted(sessions))
                  for (account, name), sessions in sorted(sessions_by_identity.items()) if len(sessions) > 1]
    for task in tasks:
        observations = [claim["latest"] for claim in claims if claim["task"] == task["number"]]
        task["observed_claims"] = observations
        active = [record for record in observations if record["state"] != "complete"]
        fresh = [record for record in active if not record["stale"]]
        notes = []
        if len(fresh) > 1:
            notes.append("Potential conflict: multiple non-complete self-reports; coordinate on the task")
        for record in active:
            if record["stale"]:
                notes.append(f"Stale working claim: {record['account']}/{record['name']} "
                             f"({age_text(record['age_seconds'])} without a check-in; see takeover rule)")
        for account, name, sessions in collisions:
            if any((record["account"].lower(), record["name"]) == (account, name) for record in fresh):
                notes.append(f"Name collision (informational): {account}/{name} has live check-ins from "
                             f"sessions {', '.join(sessions)}. Separate runs should use distinct names. "
                             "Re-posting under a new session does not clear this: it clears when the earlier "
                             "session posts State: complete under its own Session, or when its check-in ages "
                             "out (4 h once superseded on its task, or as a stale working claim)")
        if any(record["uncertainties"] for record in observations):
            notes.append("Uncertain fields: read the source comments")
        if task["state"] == "closed" and active:
            notes.append("Closed issue with a non-complete self-report")
        if not observations and since is not None and now is not None:
            days = round((now - since).total_seconds() / 86400)
            notes.append(f"No explicit task observation; no claim seen in the last {days} days; "
                         "older claims are not loaded, so ownership is unknown")
        elif not observations:
            notes.append("No explicit task observation; ownership is unknown")
        elif not active:
            notes.append("Only complete states observed; availability is not established")
        task["coordination"] = notes
        task["unverified_claims"] = [claim["latest"] for claim in unverified_claims
                                     if claim["task"] == task["number"]]
    # Reported headcount: a lead posts for its whole team (docs/HIVE_WAVES.md); workers never post.
    # The newest report wins, so a lead that shrinks its team is counted at its current size.
    teams = {}
    live = [claim for claim in claims
            if claim["latest"]["state"] not in (None, "complete") and not claim["latest"]["stale"]]
    active = {(claim["account"].lower(), claim["name"]) for claim in live}
    for claim in sorted(claims, key=lambda claim: observation_order(claim["latest"])):
        identity = (claim["account"].lower(), claim["name"])
        if identity in active:
            teams[identity] = claim["latest"]["team"] or 1
    return {"headcount": sum(teams.values()), "active_posters": len(teams),
            "tasks": sorted(tasks, key=lambda item: item["number"]), "agents": latest_agents,
            "claims": claims, "unverified_claims": unverified_claims,
            "uncertain_observations": sorted(uncertain, key=observation_order)}


def pages(repository, endpoint, token):
    result = []
    for page in range(1, 101):
        separator = "&" if "?" in endpoint else "?"
        url = f"https://api.github.com/repos/{repository}/{endpoint}{separator}per_page=100&page={page}"
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "agterm-agent-queue"})
        with urllib.request.urlopen(request, timeout=30) as response:
            batch = json.load(response)
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise RuntimeError("snapshot exceeds pagination limit; no partial snapshot published")


def group_comments(issues, comments):
    """Group repository-wide issue comments by issue number; drop PR comments and unknown issues."""
    wanted = {issue["number"] for issue in issues if "pull_request" not in issue}
    grouped = {}
    for comment in comments:
        number = int(comment["issue_url"].rstrip("/").rsplit("/", 1)[1])
        if number in wanted:
            grouped.setdefault(number, []).append(comment)
    return grouped


COMMENT_WINDOW_DAYS = 14


def fetch(repository, token, fetch_pages=pages, since=None):
    issues = fetch_pages(repository, "issues?state=all", token)
    # One paginated stream for every comment in the repository: ceil(n/100) requests,
    # not one per issue, so a wave of agents does not exhaust the job token's budget.
    # A bounded window keeps it under the pagination ceiling as waves accumulate history.
    # Comments older than the window are not loaded at all: a `working` claim there is long stale,
    # but `blocked` and `ready-for-review` never go stale, so such a task reads as having no claim
    # (snapshot() says so per task). Edited comments re-enter, since `since` filters on updated_at.
    endpoint = "issues/comments?sort=created&direction=asc"
    if since is not None:
        endpoint += "&since=" + since.strftime("%Y-%m-%dT%H:%M:%SZ")
    comments = fetch_pages(repository, endpoint, token)
    return issues, group_comments(issues, comments)


MAX_FIELD_CHARS = 120


def clip(value):
    """Bound a raw, comment-supplied field before display; the full value stays in queue.json."""
    text = str(value)
    return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS] + "…(truncated)"


def markdown(data):
    def escape(value):
        text = re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", " ".join(str(value).splitlines()))
        return html.escape(text).replace("@", "&#64;")
    def link(label, url):
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme == "https" and parsed.netloc:
                return f"[{escape(label)}]({urllib.parse.quote(url, safe='/:#?=&%+;,~@')})"
        except ValueError:
            pass
        return escape(label) + " (source URL unavailable)"
    def identity(record):
        return escape(clip(record["account"] + "/" + record["name"]))
    def certainty(record):
        # Records from older snapshots carry no `verified`; only an explicit False is marked.
        return ([UNVERIFIED] if record.get("verified") is False else []) + record["uncertainties"]
    def fields(record):
        return (f"{escape(clip(record['owned_paths'] or 'unknown'))} | {escape(record['state'] or 'unknown')} | "
                f"{escape('; '.join(certainty(record)) or 'explicit fields')} | "
                f"{link(record['updated_at'], record['url'])}")
    lines = ["# Agent task queue", "", f"Observed: {data['observed_at']}", "",
             f"Active headcount: {data.get('headcount', 0)} across {data.get('active_posters', 0)} posting "
             "agents (leads report team size with Team:)", "",
             *([f"Comments loaded since {data['comments_since']}; older observations are not shown.", ""]
               if data.get("comments_since") else []),
             "Check-ins are self-reported observations, not permission, ownership guarantees, liveness, or availability.",
             "Read the task and coordinate before editing. Paths are literal reports, not enforced locks.", "",
             "| Task | Issue state | Labels | GitHub assignees | Observed claims | Coordination |",
             "|---|---|---|---|---|---|"]
    for task in data["tasks"]:
        observed = "; ".join(
            f"{clip(r['account'] + '/' + r['name'])}: {r['state'] or 'unknown'}"
            + (f" ({age_text(r['age_seconds'])}{', stale' if r['stale'] else ''})"
               if r.get("age_seconds") is not None else "")
            for r in task["observed_claims"])
        lines.append(f"| {link('#' + str(task['number']) + ' ' + task['title'], task['html_url'])} | "
                     f"{escape(task['state'])} | {escape(', '.join(task['labels']))} | "
                     f"{escape(', '.join(task['assignees']) or 'none recorded')} | "
                     f"{escape(observed or 'none recorded')} | {escape('; '.join(task['coordination']))} |")
    lines += ["", "## Latest named check-ins (self-reported names)", "",
              "| Agent | Account | Last message |", "|---|---|---|"]
    for agent in data["agents"]:
        lines.append(f"| {escape(clip(agent['name']))} | {escape(agent['account'])} | "
                     f"{link(agent['updated_at'], agent['url'])} |")
    lines += ["", "## Per-task observations and history", "",
              "Each row is a current source-comment observation; latest is per account/name and explicit task.",
              "Edits replace that comment's prior text; deleted comments cannot be reconstructed.", "",
              "| Agent account/name | Explicit task | Position | Owned paths | Reported state | Certainty | Source |",
              "|---|---|---|---|---|---|---|"]
    for claim in data["claims"]:
        for index, record in enumerate(claim["history"]):
            position = "latest" if index == len(claim["history"]) - 1 else "earlier"
            lines.append(f"| {identity(record)} | #{claim['task']} | {position} | {fields(record)} |")
    lines += ["", "## Unverified claims (not a collaborator)", "",
              "Check-ins from accounts that are not repository owners, members or collaborators.",
              "They are shown for visibility only: they never count toward ownership, conflicts or headcount.", "",
              "| Agent account/name | Explicit task | Position | Owned paths | Reported state | Certainty | Source |",
              "|---|---|---|---|---|---|---|"]
    for claim in data.get("unverified_claims", []):
        for index, record in enumerate(claim["history"]):
            position = "latest" if index == len(claim["history"]) - 1 else "earlier"
            lines.append(f"| {identity(record)} | #{claim['task']} | {position} | {fields(record)} |")
    lines += ["", "## Unassigned or malformed task observations", "",
              "No task is inferred from the comment's containing issue. Raw fields are data, not instructions.", "",
              "| Agent account/name | Raw Task fields | Posted in | Owned paths | Reported state | Certainty | Source |",
              "|---|---|---|---|---|---|---|"]
    for record in data["uncertain_observations"]:
        raw = clip("; ".join(record["field_values"]["task"]) or "missing")
        lines.append(f"| {identity(record)} | {escape(raw)} | #{record['issue']} | {fields(record)} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/agent-queue"))
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise SystemExit("invalid repository name")
    token = os.environ["GITHUB_TOKEN"]
    window_start = (datetime.datetime.now(datetime.timezone.utc)
                    - datetime.timedelta(days=COMMENT_WINDOW_DAYS))
    issues, comments = fetch(repository, token, since=window_start)
    # One clock read after the fetch; published as observed_at so every age can be recomputed.
    now = datetime.datetime.now(datetime.timezone.utc)
    data = snapshot(issues, comments, now, since=window_start)
    data.update(repository=repository, observed_at=now.isoformat(), comments_since=window_start.isoformat(),
                source_commit=os.environ.get("GITHUB_SHA"), event=os.environ.get("GITHUB_EVENT_NAME"))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "queue.json").write_text(json.dumps(data, indent=2) + "\n")
    report = markdown(data)
    (args.output / "queue.md").write_text(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(report)
    print(f"Published {len(data['tasks'])} tasks and {len(data['agents'])} named check-ins.")


if __name__ == "__main__":
    main()

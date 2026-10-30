"""String-only model of quoted-path membership; does not execute hook commands or Swift."""
import json

def quote(value):
    return "'" + value.replace("'", "'\\''") + "'"

script_dir = "/tmp/agent's configuration/agent-status"
paths = [script_dir + "/agterm-claude-status.sh", script_dir + "/agterm-agent-status.sh"]
command = quote(paths[0]) + " active --blink"
command = json.loads(json.dumps({"command": command}))["command"]
old_detected = any(path in command for path in paths)
fixed_detected = any(probe in command for path in paths for probe in (path, quote(path)))
assert not old_detected and fixed_detected
print(json.dumps({"raw_probe_matches_generated_command": old_detected,
                  "quoted_probe_matches_generated_command": fixed_detected,
                  "scope": "Python string-only membership model, not Swift execution"}))

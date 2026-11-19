#!/usr/bin/env python3
"""One-off: fold the theme license research (themes.json + licenses/) into the notice profile."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

repo, research = Path(sys.argv[1]), Path(sys.argv[2])
decisions = json.loads((research / sys.argv[3]).read_text())
notices = repo / 'packaging/linux/notices'
inventory_path, sources_path = notices / 'inventory.json', notices / 'sources.json'
inventory = json.loads(inventory_path.read_text())
sources = json.loads(sources_path.read_text())

for old in sorted(notices.glob('theme-*')):
    shutil.rmtree(old)
sources['files'] = [entry for entry in sources['files'] if not entry['component'].startswith('theme-')]

assert set(decisions) == {theme['file'] for theme in inventory['themes']['files']}, 'decision keys differ'
added = {}
for theme in inventory['themes']['files']:
    decision = decisions[theme['file']]
    for key in ('attribution', 'license', 'reason'):
        theme.pop(key, None)
    if decision['status'] != 'resolved':
        theme['attribution'] = 'dropped'
        reason = decision['reason'].strip().rstrip('.')
        theme['reason'] = reason[0].upper() + reason[1:]
        continue
    repository, revision, path = decision['repository'], decision['revision'], decision['license_path']
    slug = 'theme-' + re.sub(r'[^a-z0-9.-]+', '-', repository.lower().replace('/', '-'))
    notice = f'{slug}/{Path(path).name}'
    data = (research / decision['license_file']).read_bytes()
    assert hashlib.sha256(data).hexdigest() == decision['license_sha256'], decision
    if notice not in added:
        (notices / slug).mkdir(exist_ok=True)
        (notices / notice).write_bytes(data)
        added[notice] = {'component': slug, 'repository': repository, 'revision': revision, 'source_path': path,
                         'url': f'https://raw.githubusercontent.com/{repository}/{revision}/{path}',
                         'file': notice, 'sha256': decision['license_sha256']}
    else:
        assert added[notice]['revision'] == revision and added[notice]['sha256'] == decision['license_sha256']
    license = {'spdx': decision['spdx'], 'notice': notice, 'repository': repository, 'revision': revision}
    if decision.get('basis'):
        license['basis'] = decision['basis']
    theme['attribution'] = 'resolved'
    theme['license'] = license

# Apache-2.0 section 4(d): a NOTICE shipped by the licensor travels with the work.
EXTRA = {'TriumphInteractive/theebest-themes': ['NOTICE']}
for entry in list(added.values()):
    for name in EXTRA.get(entry['repository'], []):
        data = (research / 'extra' / entry['repository'].replace('/', '__') / name).read_bytes()
        notice = f"{entry['component']}/{name}"
        (notices / notice).write_bytes(data)
        added[notice] = dict(entry, source_path=name, file=notice, sha256=hashlib.sha256(data).hexdigest(),
                             url=f"https://raw.githubusercontent.com/{entry['repository']}/{entry['revision']}/{name}")
sources['files'] += sorted(added.values(), key=lambda entry: entry['file'])
inventory['components']['ghostty-theme-authors'] = {
    'notices': sorted(added),
    'association': 'share/ghostty/themes: author license of each shipped theme; THEMES.md maps theme to notice',
}
inventory['unresolved'][0] = ('Themes without a pinned redistributable author license are dropped from the payload; '
                              'THEMES.md lists each with its reason.')
inventory_bytes = (json.dumps(inventory, indent=2, ensure_ascii=False) + '\n').encode()
inventory_path.write_bytes(inventory_bytes)
sources['inventory_sha256'] = hashlib.sha256(inventory_bytes).hexdigest()
sources_path.write_text(json.dumps(sources, indent=2, ensure_ascii=False) + '\n')
themes = subprocess.run([sys.executable, '-I', str(repo / 'scripts/linux-notices.py'), 'themes'],
                        check=True, capture_output=True, text=True).stdout
(notices / 'themes.md').write_text(themes)
shipped = sum(theme['attribution'] == 'resolved' for theme in inventory['themes']['files'])
print(f'{shipped} shipped, {len(added)} notices')

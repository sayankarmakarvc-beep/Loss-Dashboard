"""
Minimal GitHub Contents API helper for pushing files to sayankarmakarvc-beep/Loss-Dashboard
without requiring git to be installed locally.
"""
import base64
import requests

TOKEN_FILE = r'C:\Users\sayankarmakar.vc\Desktop\Python\Credentials\github_token.txt'
OWNER = 'sayankarmakarvc-beep'
REPO = 'Loss-Dashboard'
API_ROOT = f'https://api.github.com/repos/{OWNER}/{REPO}'


def _headers():
    with open(TOKEN_FILE, 'r') as f:
        token = f.read().strip()
    return {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'loss-dashboard-bot',
    }


def put_file(path, content_bytes, message, branch='main'):
    """Create or update a file in the repo via the Contents API. Returns the API response dict."""
    url = f'{API_ROOT}/contents/{path}'
    headers = _headers()

    sha = None
    r = requests.get(url, headers=headers, params={'ref': branch})
    if r.status_code == 200:
        sha = r.json()['sha']
    elif r.status_code != 404:
        r.raise_for_status()

    payload = {
        'message': message,
        'content': base64.b64encode(content_bytes).decode('ascii'),
        'branch': branch,
    }
    if sha:
        payload['sha'] = sha

    resp = requests.put(url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()

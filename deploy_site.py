"""
One-off / on-demand deploy of the static dashboard shell (index.html) to
GitHub. Run this whenever index.html changes; pull_data.py handles the
recurring data.json refresh separately.
"""
import os
import github_api

HERE = os.path.dirname(os.path.abspath(__file__))


def deploy():
    with open(os.path.join(HERE, 'index.html'), 'rb') as f:
        html = f.read()
    result = github_api.put_file('index.html', html, 'Deploy dashboard index.html')
    print('Pushed index.html, commit', result['commit']['sha'][:8])


if __name__ == '__main__':
    deploy()

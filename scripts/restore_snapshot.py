"""Restore the newest same-repository Actions snapshot, with safe ZIP extraction.

Uses only the workflow's short-lived read token, never a paid service.
"""
import io
import json
import os
from pathlib import Path,PurePosixPath
import urllib.request
import zipfile


def main():
    repo=os.environ['GITHUB_REPOSITORY']; token=os.environ['GH_TOKEN']
    def get(url):
        req=urllib.request.Request(url,headers={'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        return urllib.request.urlopen(req,timeout=60).read()
    payload=json.loads(get(f'https://api.github.com/repos/{repo}/actions/artifacts?per_page=100'))
    candidates=[a for a in payload['artifacts'] if a['name'].startswith('music-deals-') and not a['expired'] and a.get('workflow_run',{}).get('head_branch')=='main']
    if not candidates:
        print('No previous main-branch snapshot; starting fresh'); return
    artifact=max(candidates,key=lambda a:a['created_at'])
    data=get(artifact['archive_download_url'])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for item in z.infolist():
            p=PurePosixPath(item.filename)
            if p.is_absolute() or '..' in p.parts: raise ValueError('Unsafe archive path')
            if not str(p).startswith('data/') or item.is_dir(): continue
            if item.file_size>200_000_000: raise ValueError('Oversized archive entry')
            target=Path(*p.parts); target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(z.read(item))
    print(f'Restored snapshot {artifact["id"]} from {artifact["created_at"]}')


if __name__=='__main__': main()

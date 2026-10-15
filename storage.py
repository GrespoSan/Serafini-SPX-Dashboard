"""Client server-side per il bucket privato di Supabase Storage."""
import json
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


class Store:
    def __init__(self, url: str, key: str, bucket: str = 'spx-snapshots'):
        self.url = url.rstrip('/')
        self.key = key
        self.bucket = bucket

    def _req(self, method: str, path: str, data=None, mime='application/json') -> bytes:
        headers = {'apikey': self.key, 'Authorization': f'Bearer {self.key}'}
        if data is not None:
            headers['Content-Type'] = mime
        request = Request(self.url + '/storage/v1' + path, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=90) as response:
                return response.read()
        except HTTPError as exc:
            # Non includere il corpo della risposta: potrebbe contenere informazioni sensibili.
            raise RuntimeError(f'Supabase HTTP {exc.code}') from exc

    def list(self, prefix: str):
        found, offset = [], 0
        while True:
            params = json.dumps({'prefix': prefix, 'limit': 100, 'offset': offset,
                                 'sortBy': {'column': 'name', 'order': 'asc'}}).encode()
            items = json.loads(self._req('POST', '/object/list/' + quote(self.bucket, safe=''), params))
            found.extend(x for x in items if x.get('name') and x.get('id'))
            if len(items) < 100:
                break
            offset += len(items)
        return found

    def download(self, path: str) -> bytes:
        endpoint = '/object/authenticated/' + quote(self.bucket, safe='') + '/' + quote(path, safe='/')
        return self._req('GET', endpoint)

    def delete(self, paths: list[str]) -> None:
        # Supabase Storage remove batch, max 1000 paths. App limits batch to 100.
        if paths:
            self._req('DELETE', '/object/' + quote(self.bucket, safe=''),
                      json.dumps({'prefixes': paths}).encode())

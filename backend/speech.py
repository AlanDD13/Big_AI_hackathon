"""Native xAI speech for server-authored notices, never browser-supplied text."""
import asyncio
from collections import OrderedDict
import hashlib

import httpx


class SpeechUnavailable(RuntimeError):
    pass


class GrokSpeech:
    def __init__(self, key='', *, voice='eve', language='en', client=None):
        self._key, self.voice, self.language = key, voice, language
        self._client = client
        self._cache = OrderedDict()
        self._lock = asyncio.Lock()
        self.used = False

    @property
    def configured(self):
        return bool(self._key)

    async def audio(self, text):
        if not self.configured or not isinstance(text, str) or not 0 < len(text) <= 500:
            raise SpeechUnavailable('Voice unavailable')
        cache_key = hashlib.sha256(f'{self.voice}:{self.language}:{text}'.encode()).hexdigest()
        async with self._lock:
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                return self._cache[cache_key]
            try:
                async with asyncio.timeout(12):
                    if self._client is None:
                        async with httpx.AsyncClient(follow_redirects=False) as client:
                            data = await self._request(client, text)
                    else:
                        data = await self._request(self._client, text)
            except (httpx.HTTPError, TimeoutError, ValueError):
                raise SpeechUnavailable('Voice unavailable') from None
            self.used = True
            self._cache[cache_key] = data
            if len(self._cache) > 24:
                self._cache.popitem(last=False)
            return data

    async def _request(self, client, text):
        async with client.stream('POST', 'https://api.x.ai/v1/tts',
                headers={'Authorization': f'Bearer {self._key}', 'Accept-Encoding': 'identity'},
                json={'text': text, 'voice_id': self.voice, 'language': self.language},
                timeout=10, follow_redirects=False) as response:
            if (response.status_code != 200
                    or response.headers.get('content-encoding', 'identity') != 'identity'
                    or response.headers.get('content-type', '').split(';')[0] not in ('audio/mpeg', 'audio/mp3')):
                raise SpeechUnavailable('Voice unavailable')
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > 2 * 1024 * 1024:
                    raise SpeechUnavailable('Voice unavailable')
                data.extend(chunk)
            if not data:
                raise SpeechUnavailable('Voice unavailable')
            return bytes(data)

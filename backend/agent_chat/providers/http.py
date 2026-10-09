import requests
from .base import ProviderError, ProviderTimeout
from .credentials import read_provider_key

class HTTPProvider:
    def __init__(self, key_file, transport_factory=requests.Session):
        self.key_file = key_file
        self.transport_factory = transport_factory

    def request(self, url, body, headers, timeout):
        transport = self.transport_factory()
        transport.trust_env = False
        response = None
        try:
            response = transport.post(url, json=body, headers=headers, timeout=timeout, allow_redirects=False)
            if response.status_code != 200:
                raise ProviderError()
            return response.json()
        except requests.Timeout:
            raise ProviderTimeout("Chat model timed out.") from None
        except Exception as error:
            if isinstance(error, ProviderError):
                raise
            raise ProviderError() from None
        finally:
            if response is not None:
                response.close()
            transport.close()

    def key(self):
        return read_provider_key(self.key_file)

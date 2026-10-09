"""Constant-time private MCP transport auth. No cookies or client actor fields."""
import hmac

class BearerAuth:
    def __init__(self,app,token): self.app,self.token=app,token
    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        headers=dict(scope['headers'])
        got=headers.get(b'authorization',b'').decode('latin1')
        if headers.get(b'origin') or not hmac.compare_digest(got,'Bearer '+self.token):
            await send({'type':'http.response.start','status':401,'headers':[(b'content-type',b'application/json')]})
            return await send({'type':'http.response.body','body':b'{"error":"Authentication required."}'})
        return await self.app(scope,receive,send)

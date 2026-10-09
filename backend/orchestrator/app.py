"""Authenticated AgentBridge coordinator control application."""
import hmac
from flask import Flask,request,jsonify
from ..agent_chat.store import StoreError


def create_app(service,token_principals,allowed_origins=()):
    if not token_principals or any(not token or not owner for token,owner in token_principals.items()):
        raise ValueError('Authenticated principals are required.')
    app=Flask(__name__); app.config['MAX_CONTENT_LENGTH']=65536

    @app.before_request
    def authorize():
        origin=request.headers.get('Origin')
        if origin and origin not in allowed_origins: return jsonify(error='Origin denied.'),403
        supplied=request.headers.get('Authorization','')
        request.owner=None
        for token,owner in token_principals.items():
            if hmac.compare_digest(supplied,'Bearer '+token): request.owner=owner
        if request.owner is None: return jsonify(error='Authentication required.'),401

    @app.errorhandler(StoreError)
    def domain_error(error): return jsonify(error=str(error)),error.status

    @app.errorhandler(Exception)
    def unexpected(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error,HTTPException): return jsonify(error='Request rejected.'),error.code
        return jsonify(error='Orchestrator operation failed.'),500

    @app.post('/api/orchestrator/runs')
    def create(): return jsonify(service.create(request.owner,request.get_json())),201

    @app.get('/api/orchestrator/runs/<rid>')
    def inspect(rid): return jsonify(service.view(request.owner,rid))

    @app.post('/api/orchestrator/runs/<rid>/<action>')
    def control(rid,action):
        if action not in ('pause','continue','interrupt','stop','input','approve'): return jsonify(error='Unknown action.'),404
        return jsonify(service.control(request.owner,rid,action,request.get_json()))
    return app

import json
from flask import Blueprint, Response, g, jsonify, request, stream_with_context
from .store import AgentChatStore, StoreError
from .service import AgentChatService
from .providers.base import ProviderError
from .bridge_client import BridgeError

def create_blueprint(service=None):
    blueprint = Blueprint("agent_chat", __name__, url_prefix="/api/agent")
    # Lazy initialization avoids creating state during app import/startup.
    state = {"service": service}

    def svc():
        if state["service"] is None:
            state["service"] = AgentChatService(AgentChatStore())
        return state["service"]

    def owner():
        actor = getattr(g, "rag_actor", None)
        if not actor or actor == "unknown":
            raise StoreError("Authentication is required.", 401)
        return actor

    def body(required):
        value = request.get_json(silent=True)
        if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k] for k in required):
            raise StoreError("Invalid Agent Chat request.", 400)
        return value

    @blueprint.errorhandler(StoreError)
    def store_error(error):
        return jsonify(error="agent_chat_request", message=str(error)), error.status

    @blueprint.errorhandler(ProviderError)
    def provider_error(error):
        return jsonify(error=error.code, message="Selected chat model is unavailable or not configured."), 503

    @blueprint.errorhandler(BridgeError)
    def bridge_error(error):
        return jsonify(error=error.code, message="Agent execution is unavailable."), 503

    @blueprint.get("/sessions")
    def list_sessions():
        return jsonify(svc().store.list_sessions(owner()))

    @blueprint.post("/sessions")
    def create_session():
        data = body(("chat_model", "agent"))
        return jsonify(svc().store.create_session(owner(), data["chat_model"], data["agent"], data.get("title", "New Agent Chat"))), 201

    @blueprint.patch("/sessions/<session_id>")
    def update_session(session_id):
        data = body(("chat_model", "agent"))
        return jsonify(svc().store.update_selection(owner(), session_id, data["chat_model"], data["agent"]))

    @blueprint.get("/sessions/<session_id>/messages")
    def messages(session_id):
        return jsonify(svc().store.get_messages(owner(), session_id))

    @blueprint.get("/sessions/<session_id>/runs/<run_id>")
    def run_state(session_id, run_id):
        svc().store.get_session(owner(), session_id)
        run = svc().store.get_run(run_id)
        if run["session_id"] != session_id:
            raise StoreError("Agent run not found.", 404)
        return jsonify(run)

    @blueprint.post("/chat/stream")
    def stream():
        data = body(("session_id", "chat_model", "agent", "message"))
        actor = owner()
        session = svc().store.get_session(actor, data["session_id"])
        message_id = data.get("message_id")
        if message_id is not None and (not isinstance(message_id, str) or not 1 <= len(message_id) <= 64):
            raise StoreError("Invalid message identifier.", 400)
        service = svc()
        if (session["chat_model"], session["agent"]) != (data["chat_model"], data["agent"]):
            service.store.update_selection(actor, session["session_id"], data["chat_model"], data["agent"])
        run = service.begin(actor, session["session_id"], data["message"], message_id)
        @stream_with_context
        def generate():
            execution = service.execute(run["run_id"])
            try:
                for event in execution:
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            finally:
                execution.close()
        return Response(generate(), mimetype="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @blueprint.post("/chat/cancel")
    def cancel():
        data = body(("session_id", "run_id"))
        return jsonify(svc().cancel(owner(), data["session_id"], data["run_id"]))

    return blueprint

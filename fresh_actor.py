"""Give every harness session its own memory actor.

The managed harness has AgentCore managed memory enabled. Long-term memory is
scoped by `actorId`; the starter scripts never pass one, so every conversation
shares one actor and facts leak between sessions (e.g. an old ticket ID
reappearing in a brand-new conversation, or an earlier test prompt bleeding
into the next one).

Importing this module registers a botocore event hook that adds
`actorId = <runtimeSessionId>` to every InvokeHarness call that does not set
one, so each session (each chat.py run, each eval test case) is isolated.

Usage:
    python -c "import fresh_actor, runpy, sys; sys.argv=['chat.py']; runpy.run_path('chat.py', run_name='__main__')"
or simply `import fresh_actor` before creating boto3 clients (run_transcripts.py
and run_with_fresh_actor.py do this).
"""

import boto3


def _add_actor(params, **_):
    if "actorId" not in params and params.get("runtimeSessionId"):
        # actorId must be short and simple; derive it from the session id.
        params["actorId"] = "customer-" + params["runtimeSessionId"].split("-")[0]


boto3.DEFAULT_SESSION or boto3.setup_default_session()
boto3.DEFAULT_SESSION.events.register(
    "provide-client-params.bedrock-agentcore.InvokeHarness", _add_actor
)

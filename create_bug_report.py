"""Lambda function behind the AgentCore Gateway `create_bug_report` tool.

This file mirrors the code embedded in cloudformation-tool.yaml — if you
change one, keep the other in sync.

How the gateway calls this function:

* The **tool arguments** arrive directly as the Lambda ``event`` — a plain
  JSON object such as::

      {"description": "...", "stepsToReproduce": "...", "environment": "..."}

  (Bedrock Agents Classic used to wrap arguments in a ``messageVersion`` /
  ``parameters`` envelope; the gateway does not.)

* The **tool name** arrives via the Lambda client context:
  ``context.client_context.custom["bedrockAgentCoreToolName"]``, namespaced
  as ``<targetName>___<toolName>`` (three underscores).

* Whatever the handler **returns** is passed back to the model as the tool
  result.

Tip: the ``print("EVENT:", ...)`` below writes the raw event to CloudWatch
Logs (log group ``/aws/lambda/<function-name>``). Check it after your first
test invoke to see the real event shape.
"""

import json
import os
import uuid
from datetime import datetime, timezone
import boto3

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _tool_name(context):
    """AgentCore Gateway passes the tool name via the Lambda client
    context, namespaced as "<targetName>___<toolName>"."""
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None) or {}
    name = custom.get("bedrockAgentCoreToolName", "")
    return name.split("___")[-1] if "___" in name else name


PLACEHOLDERS = {"missing", "unknown", "n/a", "na", "none", "null", "tbd", "not provided",
                "not provided yet", "not yet provided", "test", "-", "?"}


def _is_real(value):
    """Reject empty, placeholder or junk values the model sometimes invents
    for a required field it has not collected from the customer yet."""
    v = value.strip().strip(".").lower()
    return len(v) >= 5 and v not in PLACEHOLDERS and any(c.isalnum() for c in v)


def lambda_handler(event, context):
    # The gateway sends the tool arguments directly as the event.
    # Keep this print: it shows the exact event shape in CloudWatch
    # Logs, which is invaluable when debugging your first invoke.
    print("EVENT:", json.dumps(event, default=str))

    tool_name = _tool_name(context)
    print("TOOL NAME:", tool_name)
    if tool_name and tool_name != "create_bug_report":
        return {"error": f"unsupported tool: {tool_name}"}

    if not isinstance(event, dict):
        return {"error": "unexpected event shape", "event": str(event)}

    description = str(event.get("description") or "").strip()
    steps = str(event.get("stepsToReproduce") or "").strip()
    environment = str(event.get("environment") or "").strip()

    # All three fields are required and must be non-empty. The model
    # sometimes tries to satisfy a "required" parameter with an empty
    # string — returning an error here tells it to go back and ask the
    # customer instead of filing an incomplete ticket.
    missing = [name for name, value in [("description", description),
                                        ("stepsToReproduce", steps),
                                        ("environment", environment)] if not _is_real(value)]
    if missing:
        return {"error": "missing or placeholder value for required field(s): " + ", ".join(missing)
                         + ". Do not guess. Ask the customer for this information, then call the tool again."}

    ticket_id = str(uuid.uuid4())
    item = {
        "ticketId": ticket_id,
        "description": description,
        "stepsToReproduce": steps,
        "environment": environment,
        "status": "OPEN",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    table.put_item(Item=item)

    # Whatever this returns is handed back to the model as the
    # tool result.
    return {"ticketId": ticket_id, "status": "OPEN"}

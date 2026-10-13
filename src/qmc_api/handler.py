"""API router Lambda. Stub (G0a) so WS1 can build and deploy; WS2 replaces it."""

import json


def handle(event, context):
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"status": "stub", "fn": "api"}),
    }

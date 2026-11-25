"""Lambda, every minute (EventBridge): expire HELD holds whose expiry message never did."""
import json

from stockhold import expiry


def handler(event, context):
    r = expiry.sweep()
    print(json.dumps({"fn": "sweeper", **r}))
    return r

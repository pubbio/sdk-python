import os

from pubb import PubbServer

pubb = PubbServer(os.environ["PUBB_APP_ID"], os.environ["PUBB_APP_KEY"], os.environ["PUBB_APP_SECRET"])
result = pubb.trigger("updates", "message", {"text": "Hello from Python"})
print(result.publication_id)  # Accepted for processing, not a delivery receipt.

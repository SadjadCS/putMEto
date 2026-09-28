"""Start the local application: python run.py."""

import os

import uvicorn


if __name__ == "__main__":
    port = int(os.environ.get("PUTMETO_PORT", "8000"))
    print(f"\nPutMeTo is available at http://127.0.0.1:{port}\n")
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, reload=False)

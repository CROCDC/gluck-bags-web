import os

from app import app

if __name__ == "__main__":
    # Debug on by default for local `python run.py`; production sets FLASK_DEBUG=0.
    debug = os.environ.get("FLASK_DEBUG", "1") not in ("0", "false", "False", "")
    app.run(host="0.0.0.0", port=7010, debug=debug)

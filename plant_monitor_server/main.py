import functions_framework
import time
import json
import logging
from datetime import datetime, timezone, timedelta
from google.cloud import firestore, storage
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---- 部署前請修改這裡 ----
PROJECT_ID = ""
DATABASE_NAME = "esp32"
BUCKET_NAME = ""
# ---------------------------

# simple auth header for ESP32 to send data to server
# Change it to your own value
AUTH_HEADER = "ggininder"
AUTH_VALUE = "yes-GGININDER"


def _get_firestore():
    return firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)


def _auth(request):
    if request.headers.get(AUTH_HEADER) != AUTH_VALUE:
        return ("Unauthorized", 401)
    return None


def _handle_sensor(request):
    data = request.get_json(silent=True)
    if data is None:
        return ("Invalid JSON", 400)

    required = ["temperature", "humidity", "soil_moist"]
    missing = [k for k in required if k not in data]
    if missing:
        return (f"Missing fields: {', '.join(missing)}", 400)

    try:
        temperature = float(data["temperature"])
        humidity = float(data["humidity"])
        soil_moist = float(data["soil_moist"])
    except (ValueError, TypeError):
        return ("All fields must be numeric", 400)

    record = {
        "timestamp": datetime.now(timezone.utc),
        "temperature": temperature,
        "humidity": humidity,
        "soil_moist": soil_moist,
    }

    _get_firestore().collection("sensor_data").add(record)

    log_data = record.copy()
    log_data["timestamp"] = int(time.time())
    logger.info(json.dumps(log_data))

    return ("OK", 200)


def _handle_upload(request):
    file = request.files.get("file")
    if file is None:
        return ("No file provided", 400)

    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "bin"

    tz_utc8 = timezone(timedelta(hours=8))

    blob_name = (
        f"esp32/"
        f"{datetime.now(tz_utc8).strftime('%Y-%m-%d-%H-%M-%S')}.{ext}"
    )

    bucket = storage.Client(project=PROJECT_ID).bucket(BUCKET_NAME)
    blob = bucket.blob(blob_name)
    blob.upload_from_file(file, content_type=file.content_type)

    logger.info(f"Uploaded: {blob_name}")
    return (blob_name, 200)


ROUTES = {
    "/sensor": _handle_sensor,
    "/upload": _handle_upload,
}


@functions_framework.http
def dispatcher(request):
    if request.method != "POST":
        return ("Only POST is accepted", 405)

    err = _auth(request)
    if err:
        return err

    handler = ROUTES.get(request.path)
    if handler is None:
        return ("Not Found", 404)

    return handler(request)
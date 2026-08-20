"""backend/media fayllarini S3-compatible storage (Cloudflare R2 ham) ga yuklaydi."""
from pathlib import Path
import mimetypes
import boto3
from app.config import settings

if not all((settings.s3_endpoint_url, settings.s3_bucket, settings.s3_access_key, settings.s3_secret_key)):
    raise SystemExit("S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY ni to'ldiring")
client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key)
root = Path(__file__).resolve().parents[1] / "media"
count = 0
for path in root.rglob("*"):
    if not path.is_file():
        continue
    key = "media/" + path.relative_to(root).as_posix()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    client.upload_file(str(path), settings.s3_bucket, key, ExtraArgs={"ContentType": content_type, "CacheControl": "public,max-age=31536000,immutable"})
    count += 1
print(f"{count} ta media yuklandi")
import boto3
from botocore.config import Config
from django.conf import settings

s3_client = boto3.client(
    's3',
    endpoint_url=settings.AWS_S3_ENDPOINT_URL,
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    config=Config(signature_version='s3v4'),
    region_name='auto'
)


def generate_presigned_url(file_name, file_type):
    # Generate a presigned POST
    return s3_client.generate_presigned_url(
        ClientMethod='put_object',
        Params={
            'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
            'Key': f"{file_name}",
            'ContentType': file_type,
        },
        ExpiresIn=3600
    )

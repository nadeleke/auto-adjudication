import os
import boto3
import urllib.parse

s3 = boto3.client('s3')
BUCKET = os.environ['BUCKET_NAME']

def handler(event, context):
    try:
        params = event.get("queryStringParameters") or {}
        filename = urllib.parse.unquote(params.get("filename", ""))
        content_type = params.get("contentType", "")

        if not filename:
            return {
                "statusCode": 400,
                "body": "Missing 'filename' parameter"
            }

        # File will be uploaded to claimcollectors11/ folder
        key = f"claimcollectors11/{filename}"

        # Build presign params
        presign_params = {
            'Bucket': BUCKET,
            'Key': key
        }
        if content_type:
            presign_params['ContentType'] = content_type

        # Generate the presigned URL
        url = s3.generate_presigned_url(
            ClientMethod='put_object',
            Params=presign_params,
            ExpiresIn=300  # 5 minutes
        )

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"  # Allow CORS
            },
            "body": f'{{ "uploadUrl": "{url}" }}'
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": f"Error generating URL: {str(e)}"
        }

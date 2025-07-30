from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
    aws_lambda as _lambda,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_s3_deployment as s3deploy,
)
from aws_cdk.aws_cloudfront import (
    Distribution,
    BehaviorOptions,
    ViewerProtocolPolicy,
    S3OriginAccessControl,
    Signing,
)
from aws_cdk.aws_cloudfront_origins import S3BucketOrigin
from aws_cdk.aws_iam import PolicyStatement, ServicePrincipal
from aws_cdk import CfnOutput
from constructs import Construct
import os

class AutoAdjudicationStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # 1) Private S3 bucket
        bucket = s3.Bucket(self, "GomspeedBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,

            cors=[
            s3.CorsRule(
                allowed_methods=[
                s3.HttpMethods.PUT,       # for the actual upload
                s3.HttpMethods.GET
                ],
                allowed_origins=[
                "https://d1yzq0vbl4rwfo.cloudfront.net"  # your CloudFront domain
                ],
                allowed_headers=["*"],
                max_age=300
            )
            ]
        )

        # 2) Allow CloudFront’s Origin Access Control to GET objects
        bucket.add_to_resource_policy(PolicyStatement(
            actions=["s3:GetObject"],
            resources=[f"{bucket.bucket_arn}/*"],
            principals=[ServicePrincipal("cloudfront.amazonaws.com")]
        ))

        

        # 4) Create the Origin Access Control for this S3 origin
        oac = S3OriginAccessControl(self, "GomspeedOAC",
            signing=Signing.SIGV4_ALWAYS   # sign all requests
        )  # :contentReference[oaicite:0]{index=0}

        # 5) Build a CloudFront origin that uses that OAC
        s3_origin = S3BucketOrigin.with_origin_access_control(bucket,
            origin_access_control=oac,
            origin_path="/static-website001gomspeed"
        )  # :contentReference[oaicite:1]{index=1}

        # 6) CloudFront distribution
        distribution = Distribution(self, "GomspeedCloudFront",
            default_root_object="index.html",
            default_behavior=BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=ViewerProtocolPolicy.REDIRECT_TO_HTTPS
            )
        )

        # 7) Deploy your static site under the prefix
        s3deploy.BucketDeployment(self, "DeployWebsite",
            sources=[s3deploy.Source.asset("website")],
            destination_bucket=bucket,
            destination_key_prefix="static-website001gomspeed",
            distribution=distribution,        # invalidate CloudFront
            distribution_paths=["/*"],        # every path
        )

        # … after distribution is created …
        CfnOutput(self, "CloudFrontUrl",
            value=distribution.distribution_domain_name,
            description="CloudFront distribution domain name"
        )


        # ✅ Lambda function (for generating presigned URLs)
        upload_lambda = _lambda.Function(self, "PresignUploadLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="upload.handler",
            code=_lambda.Code.from_asset("lambda"),
            environment={
                "BUCKET_NAME": bucket.bucket_name
            },
            timeout=Duration.seconds(10)
        )

        # ✅ Permissions
        bucket.grant_put(upload_lambda)

        # ✅ API Gateway (for invoking Lambda)
        http_api = apigw.HttpApi(self, "PresignHttpAPI",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_headers=["*"],
                allow_methods=[apigw.CorsHttpMethod.GET],
                allow_origins=["*"],  # Replace with your domain later
            )
        )

        http_api.add_routes(
            path="/presigned-url",
            methods=[apigw.HttpMethod.GET],
            integration=integrations.HttpLambdaIntegration("LambdaIntegration", upload_lambda)
        )

                # … after http_api is created …
        CfnOutput(self, "ApiUrl",
            value=http_api.url,
            description="HTTP API endpoint for presigned URLs"
        )


        # ✅ Lifecycle rule (ensure claimcollectors11/ prefix exists)
        bucket.add_lifecycle_rule(
            prefix="claimcollectors11/",
            enabled=True,
            expiration=Duration.days(30)  # ✅ Files older than 30 days will be deleted
        )

        # ✅ Output CloudFront & API URLs
        self.cloudfront_url = distribution.distribution_domain_name
        self.api_url = http_api.url

        

        